<!-- Copyright (c) 2026, Admionix and contributors -->
<!-- For license information, please see license.txt -->

<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import ApprovalCard from "./ApprovalCard.vue";
import ChatMessage from "./ChatMessage.vue";
import * as api from "./api.js";

const open = ref(false);
const messages = ref([]);
const session = ref(null);
const runName = ref(null);
const question = ref(null);
const busy = ref(false);
const error = ref(null);
const draft = ref("");
const notice = ref(null);
const recent = ref([]);
// The in-flight stream, so it can be called off when the user leaves it behind. Held
// outside `ref` because nothing renders it.
let inflight = null;
// What to run again if the last attempt failed. Null once it has succeeded, so the retry
// button cannot resend something that already went through.
const retryable = ref(null);
const showRecent = ref(false);
const reference = ref(routeReference());
const scroller = ref(null);
const composer = ref(null);
// Whether the transcript is parked at the bottom. A reply that keeps yanking the view down
// while you are reading something further up is a reply you cannot read.
const following = ref(true);

// The agents on offer, and the one this conversation runs as. A session is pinned to its
// agent when it is created, so a change only ever applies to the next new conversation —
// which is why the picker closes as soon as one is under way.
const agents = ref([]);
const agent = ref(null);
const showAgents = ref(false);
const agentTitle = computed(() => {
	const chosen = agents.value.find((a) => a.name === agent.value);
	return chosen ? chosen.title || chosen.name : __("Agent");
});
// Nothing to choose between is nothing to show.
const canSwitchAgent = computed(() => agents.value.length > 1 && !session.value && !busy.value);

// Ask the model to reason at length before answering. Per message rather than a setting,
// because it costs more and takes longer and most questions do not need it. Only offered
// where the model actually has a reasoning mode — see `api.agents`.
const think = ref(false);
const canThink = computed(() => {
	const chosen = agents.value.find((a) => a.name === agent.value);
	return !!(chosen && chosen.reasoning);
});

// The doctypes the agent can open, so the attach picker cannot point a conversation at a
// record it will only refuse to read.
const subjects = ref([]);
// Whether the subject was chosen by hand. Once it was, moving around the desk stops
// changing it underneath the user — navigating away should not silently re-aim the
// conversation at whatever form happens to be on screen.
const pinned = ref(false);
const canAttach = computed(() => subjects.value.length > 0 && !session.value && !busy.value);

// Dictation, where the browser has it. Chrome and Safari do; Firefox does not, and the
// button is hidden rather than shown broken.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const listening = ref(false);
let recogniser = null;

// Openers, so an empty panel says what it can do rather than asking the user to guess. Each
// one maps to a tool the agent actually has.
const openers = [
	__("Total sales orders this month"),
	__("Show me open opportunities"),
	__("Which customers are at risk of leaving?"),
	__("Draft a quotation"),
];

// -- context -------------------------------------------------------------------------

// The record the user is looking at, which becomes the subject of a new conversation.
function routeReference() {
	const route = frappe.get_route();
	if (route && route[0] === "Form" && route[1] && route[2]) {
		return { doctype: route[1], name: route[2] };
	}
	return null;
}

onMounted(async () => {
	// The panel is mounted once for the life of the desk, so there is nothing to
	// unsubscribe from later. (frappe's `off` cannot remove a specific handler anyway.)
	frappe.router.on("change", () => {
		if (!pinned.value) reference.value = routeReference();
	});

	// Asked for once. The list is small, changes about as often as the settings do, and a
	// failure here must not stop the panel working on the default agent.
	try {
		const offered = await api.agents();
		agents.value = offered.agents || [];
		agent.value = offered.default || null;
		subjects.value = offered.subjects || [];
	} catch (e) {
		agents.value = [];
	}
});

function chooseAgent(name) {
	agent.value = name;
	showAgents.value = false;
	focusComposer();
}

// Point the next conversation at a record the user is not currently looking at. A desk
// dialog rather than something hand-rolled: Link fields already search, respect the user's
// permissions and know how to show a record's title rather than its id.
function attach() {
	const dialog = new frappe.ui.Dialog({
		title: __("Ask about a record"),
		fields: [
			{
				fieldname: "subject_doctype",
				fieldtype: "Select",
				label: __("Type"),
				options: subjects.value,
				default: subjects.value[0],
				reqd: 1,
				onchange: () => dialog.set_value("subject_name", ""),
			},
			{
				fieldname: "subject_name",
				fieldtype: "Dynamic Link",
				label: __("Record"),
				options: "subject_doctype",
				reqd: 1,
			},
		],
		primary_action_label: __("Attach"),
		primary_action: ({ subject_doctype, subject_name }) => {
			dialog.hide();
			// A session records its subject when it is created and the server reads it from
			// there, so attaching to one already under way would change nothing. Starting a
			// fresh conversation is the only honest way to honour the click.
			if (session.value || messages.value.length) newChat();
			reference.value = { doctype: subject_doctype, name: subject_name };
			pinned.value = true;
			focusComposer();
		},
	});
	dialog.show();
}

function detach() {
	reference.value = null;
	// Counts as a choice: without this, the next route change would put it straight back.
	pinned.value = true;
}

// -- dictation -----------------------------------------------------------------------
//
// The browser's own speech recognition, typing into the composer rather than sending. What
// was heard is nearly always worth a glance before it is sent, and a misheard customer name
// that goes straight to an agent with tools is a worse outcome than an extra keypress.

function dictate() {
	if (listening.value) {
		recogniser.stop();
		return;
	}

	recogniser = new Recognition();
	recogniser.lang = frappe.boot.lang || "en";
	// Show words as they are recognised. `interimResults` is what makes it feel live rather
	// than like a long pause followed by a paragraph.
	recogniser.interimResults = true;
	recogniser.continuous = true;

	// Anything already typed is kept; dictation appends rather than replaces.
	const before = draft.value ? draft.value.trimEnd() + " " : "";
	recogniser.onresult = (event) => {
		let heard = "";
		for (let i = 0; i < event.results.length; i++) heard += event.results[i][0].transcript;
		draft.value = before + heard;
		resize();
	};
	recogniser.onerror = (event) => {
		listening.value = false;
		// "aborted" is the user pressing stop, and "no-speech" is silence. Neither is a fault.
		if (event.error !== "aborted" && event.error !== "no-speech") {
			error.value =
				event.error === "not-allowed"
					? __("Microphone access was refused.")
					: __("Dictation failed: {0}", [event.error]);
		}
	};
	recogniser.onend = () => {
		listening.value = false;
		focusComposer();
	};

	error.value = null;
	listening.value = true;
	recogniser.start();
}

// -- conversation --------------------------------------------------------------------

async function send() {
	const text = draft.value.trim();
	if (!text || busy.value) return;
	// Sending while the microphone is open would leave it dictating into an empty composer.
	if (listening.value) recogniser.stop();
	draft.value = "";
	resize();
	messages.value.push({ role: "user", content: text, at: now() });
	// Their own message always pulls the view down, wherever they had scrolled to.
	following.value = true;

	const payload = { prompt: text, session: session.value };
	// Only on a new conversation: an existing session already has its agent recorded, and
	// the server reads it from there rather than from anything the panel says now.
	if (!session.value && agent.value) payload.agent = agent.value;
	if (!session.value && reference.value) {
		payload.reference_doctype = reference.value.doctype;
		payload.reference_name = reference.value.name;
	}
	// Sent with the turn rather than stored on the session, so a run started from a
	// trigger or a playbook is never silently made slower and dearer by a panel toggle.
	if (think.value) payload.think = true;

	await consume((on, signal) => api.chat(payload, on, signal));
}

async function answer(text) {
	const pending = question.value;
	question.value = null;
	if (text !== "Approve" && text !== "Deny") {
		messages.value.push({ role: "user", content: text, at: now() });
	}
	following.value = true;

	const run = runName.value;
	await consume((on, signal) => api.answer(run, { [pending.tool_call_id]: text }, on, signal));
}

function beginReply() {
	// Read the pushed entry back out rather than keeping the object that went in. `messages`
	// is a ref, so the array holds the raw object and hands back a reactive proxy on access;
	// streaming into the raw one stores the text but never tells Vue to redraw, which is a
	// reply that arrives correctly and is never seen.
	messages.value.push({ role: "assistant", content: "", tools: [], at: now() });
	scroll();
	return messages.value[messages.value.length - 1];
}

// Only ever "when this appeared on screen", so the browser's own clock and locale are the
// honest source. Nothing here is persisted, and the transcript keeps no times of its own.
function now() {
	return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function consume(start) {
	abort();
	const controller = new AbortController();
	inflight = controller;

	const reply = beginReply();
	busy.value = true;
	error.value = null;
	notice.value = null;
	retryable.value = null;
	let failed = null;

	try {
		await start((event, data) => handle(event, data, reply), controller.signal);
	} catch (e) {
		// A stream the user themselves called off is not a failure to report.
		if (e.name !== "AbortError")
			failed = e.message || __("The assistant stopped unexpectedly.");
	} finally {
		// Nothing was said, so there is nothing to keep. Done even when a newer stream has
		// taken over, because this empty bubble is ours and nobody else will clear it.
		const spoke = reply.content || reply.tools.length;
		if (!spoke) messages.value.splice(messages.value.indexOf(reply), 1);

		if (inflight === controller) {
			inflight = null;
			busy.value = false;
			notice.value = null;
			if (failed) error.value = failed;
			// Only worth offering again when it said nothing. Once the assistant has
			// spoken, resending would say it all twice.
			if (!spoke && failed) retryable.value = start;
			scroll();
		}
	}
}

function abort() {
	if (inflight) {
		inflight.abort();
		inflight = null;
	}
}

// Calling a reply off by hand. `abort` clears `inflight`, which is what stops a superseded
// stream from resetting state that now belongs to a newer one — so when nothing follows,
// the state has to be put back here instead.
function stop() {
	if (!inflight) return;
	abort();
	busy.value = false;
	notice.value = null;
}

async function retry() {
	const again = retryable.value;
	if (again) await consume(again);
}

function handle(event, data, reply) {
	if (event === "start") {
		session.value = data.session;
		runName.value = data.run;
	} else if (event === "text") {
		reply.content += data.value;
		scroll();
	} else if (event === "tool_start") {
		reply.tools.push({ id: data.id, name: data.name, done: false, error: null });
	} else if (event === "tool_end") {
		const tool = reply.tools.find((t) => t.id === data.id);
		if (tool) {
			tool.done = true;
			tool.error = data.error;
		}
	} else if (event === "notice") {
		// Not the assistant speaking, so it stays out of the transcript — but a user who
		// is told the provider is busy is not a user staring at a frozen panel.
		notice.value = data.message;
	} else if (event === "done") {
		if (!reply.content && data.content) reply.content = data.content;
		question.value = data.status === "paused" ? data.question : null;
	} else if (event === "error") {
		error.value = data.message;
	}
}

// -- sessions ------------------------------------------------------------------------

function newChat() {
	abort();
	session.value = null;
	runName.value = null;
	messages.value = [];
	question.value = null;
	error.value = null;
	notice.value = null;
	retryable.value = null;
	busy.value = false;
	showRecent.value = false;
	following.value = true;
	// A hand-picked subject belonged to the conversation that has just been cleared, so the
	// panel goes back to following whatever the user is looking at.
	pinned.value = false;
	reference.value = routeReference();
	focusComposer();
}

async function toggleRecent() {
	showRecent.value = !showRecent.value;
	if (showRecent.value) recent.value = await api.sessions(10);
}

async function openSession(name) {
	abort();
	showRecent.value = false;
	error.value = null;
	notice.value = null;
	retryable.value = null;
	busy.value = false;
	const past = await api.history(name);
	session.value = past.session;
	// A conversation that was left on an approval is still waiting for it. Reopening has
	// to put the card back, or the run can never be answered and never finishes.
	runName.value = past.run || null;
	question.value = past.question || null;
	if (past.reference_doctype && past.reference_name) {
		// The subject was fixed when this conversation was created, so walking around the
		// desk with it open must not appear to change what it is about.
		reference.value = { doctype: past.reference_doctype, name: past.reference_name };
		pinned.value = true;
	}
	// The transcript stores the model's own message list, which carries no clock, so a
	// reopened conversation shows no times rather than invented ones.
	messages.value = past.messages.map((message) => ({
		role: message.role,
		content: message.content,
		tools: (message.tools || []).map((name) => ({ name, done: true, error: null })),
	}));
	following.value = true;
	scroll();
}

// -- chrome --------------------------------------------------------------------------

function toggle() {
	open.value = !open.value;
}

function scroll() {
	if (!following.value) return;
	nextTick(() => {
		if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
	});
}

// Reading further up means the user has taken the view over; streaming stops dragging it
// back until they return to the bottom themselves.
function onScroll() {
	const el = scroller.value;
	if (el) following.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
}

function focusComposer() {
	nextTick(() => composer.value && composer.value.focus());
}

// Grow with what is being typed, up to the point where the composer would eat the panel.
function resize() {
	nextTick(() => {
		const el = composer.value;
		if (!el) return;
		el.style.height = "auto";
		el.style.height = Math.min(el.scrollHeight, 150) + "px";
	});
}

function ask(text) {
	draft.value = text;
	send();
}

watch(open, (isOpen) => isOpen && focusComposer());

defineExpose({ toggle });
</script>

<template>
	<div class="sai-root">
		<button v-show="!open" class="sai-launcher" :title="__('Sales AI')" @click="toggle">
			<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
				<path
					fill="currentColor"
					d="M12 3a9 9 0 0 0-9 9 8.9 8.9 0 0 0 1.2 4.5L3 21l4.7-1.2A9 9 0 1 0 12 3Z"
					opacity=".18"
				/>
				<path
					fill="none"
					stroke="currentColor"
					stroke-width="1.6"
					stroke-linecap="round"
					stroke-linejoin="round"
					d="M12 3.8a8.2 8.2 0 0 0-7 12.5L3.9 20.1l3.9-1a8.2 8.2 0 1 0 4.2-15.3Z"
				/>
			</svg>
		</button>

		<transition name="sai-slide">
			<aside v-show="open" class="sai-panel">
				<header class="sai-head">
					<div class="sai-title">
						<span class="sai-status" :class="{ 'sai-status-busy': busy }"></span>
						{{ __("Sales AI") }}
					</div>
					<div class="sai-head-actions">
						<button class="sai-icon" :title="__('Recent chats')" @click="toggleRecent">
							&#9776;
						</button>
						<button class="sai-icon" :title="__('New chat')" @click="newChat">
							+
						</button>
						<button class="sai-icon" :title="__('Close')" @click="toggle">
							&times;
						</button>
					</div>
				</header>

				<div v-if="showRecent" class="sai-recent">
					<button
						v-for="item in recent"
						:key="item.name"
						class="sai-recent-item"
						@click="openSession(item.name)"
					>
						{{ item.title || item.name }}
					</button>
					<div v-if="!recent.length" class="sai-recent-empty">
						{{ __("No earlier conversations.") }}
					</div>
				</div>

				<div v-if="reference" class="sai-context">
					{{ __("About") }}
					<a
						:href="
							'/app/' +
							frappe.router.slug(reference.doctype) +
							'/' +
							encodeURIComponent(reference.name)
						"
					>
						{{ reference.name }}
					</a>
					<button
						v-if="!session"
						class="sai-context-off"
						:title="__('Do not ask about this record')"
						@click="detach"
					>
						&times;
					</button>
				</div>

				<div ref="scroller" class="sai-body" @scroll.passive="onScroll">
					<div v-if="!messages.length" class="sai-empty">
						<div class="sai-empty-title">
							{{
								__(
									"Ask about leads, opportunities, quotations or orders you can see."
								)
							}}
						</div>
						<button
							v-for="opener in openers"
							:key="opener"
							class="sai-opener"
							@click="ask(opener)"
						>
							{{ opener }}
						</button>
					</div>
					<ChatMessage v-for="(message, i) in messages" :key="i" :message="message" />
					<div v-if="busy" class="sai-thinking">
						<span class="sai-dots"><i></i><i></i><i></i></span>
						{{ notice || __("Thinking") }}
					</div>
					<ApprovalCard
						v-if="question"
						:question="question"
						:busy="busy"
						@reply="answer"
					/>
					<div v-if="error" class="sai-error">
						{{ error }}
						<button v-if="retryable && !busy" class="sai-retry" @click="retry">
							{{ __("Try again") }}
						</button>
					</div>
				</div>

				<button
					v-show="!following"
					class="sai-jump"
					:title="__('Jump to latest')"
					@click="
						following = true;
						scroll();
					"
				>
					&darr;
				</button>

				<footer class="sai-composer">
					<textarea
						ref="composer"
						v-model="draft"
						class="sai-textarea"
						rows="1"
						:placeholder="
							listening
								? __('Listening…')
								: question
								? __('Answer above to carry on')
								: __('Ask a question')
						"
						:disabled="busy || !!question"
						@input="resize"
						@keydown.enter.exact.prevent="send"
					></textarea>

					<div class="sai-tools">
						<button
							v-if="canAttach"
							class="sai-tool"
							:title="__('Ask about a record')"
							@click="attach"
						>
							+
						</button>

						<button
							v-if="canThink"
							class="sai-tool sai-tool-label"
							:class="{ 'sai-tool-on': think }"
							:title="
								__(
									'Reason at length before answering. Slower, and uses more tokens.'
								)
							"
							@click="think = !think"
						>
							<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
								<path
									fill="none"
									stroke="currentColor"
									stroke-width="1.7"
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M9 18h6m-5 3h4M12 2a7 7 0 0 0-4 12.7V16h8v-1.3A7 7 0 0 0 12 2Z"
								/>
							</svg>
							{{ __("Think") }}
						</button>

						<div v-if="canSwitchAgent" class="sai-agent">
							<button
								class="sai-tool sai-tool-label"
								@click="showAgents = !showAgents"
							>
								{{ agentTitle }}<span class="sai-caret">&#9662;</span>
							</button>
							<div v-if="showAgents" class="sai-agent-menu">
								<button
									v-for="option in agents"
									:key="option.name"
									class="sai-agent-option"
									:class="{ 'sai-agent-on': option.name === agent }"
									@click="chooseAgent(option.name)"
								>
									<span class="sai-agent-name">{{
										option.title || option.name
									}}</span>
									<span class="sai-agent-model">{{ option.model }}</span>
								</button>
							</div>
						</div>

						<span class="sai-tools-gap"></span>

						<button
							v-if="Recognition"
							class="sai-tool"
							:class="{ 'sai-tool-live': listening }"
							:title="listening ? __('Stop dictating') : __('Dictate')"
							:disabled="busy || !!question"
							@click="dictate"
						>
							<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
								<path
									fill="none"
									stroke="currentColor"
									stroke-width="1.7"
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M12 3a2.5 2.5 0 0 0-2.5 2.5v6a2.5 2.5 0 0 0 5 0v-6A2.5 2.5 0 0 0 12 3ZM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"
								/>
							</svg>
						</button>

						<button
							v-if="busy"
							class="sai-send sai-send-stop"
							:title="__('Stop')"
							@click="stop"
						>
							<span class="sai-square"></span>
						</button>
						<button
							v-else
							class="sai-send"
							:title="__('Send')"
							:disabled="!!question || !draft.trim()"
							@click="send"
						>
							&uarr;
						</button>
					</div>
				</footer>
			</aside>
		</transition>
	</div>
</template>
