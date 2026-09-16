<!-- Copyright (c) 2026, Admionix and contributors -->
<!-- For license information, please see license.txt -->

<script setup>
import { nextTick, onMounted, ref, watch } from "vue";
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

// -- context -------------------------------------------------------------------------

// The record the user is looking at, which becomes the subject of a new conversation.
function routeReference() {
	const route = frappe.get_route();
	if (route && route[0] === "Form" && route[1] && route[2]) {
		return { doctype: route[1], name: route[2] };
	}
	return null;
}

onMounted(() => {
	// The panel is mounted once for the life of the desk, so there is nothing to
	// unsubscribe from later. (frappe's `off` cannot remove a specific handler anyway.)
	frappe.router.on("change", () => (reference.value = routeReference()));
});

// -- conversation --------------------------------------------------------------------

async function send() {
	const text = draft.value.trim();
	if (!text || busy.value) return;
	draft.value = "";
	messages.value.push({ role: "user", content: text });

	const payload = { prompt: text, session: session.value };
	if (!session.value && reference.value) {
		payload.reference_doctype = reference.value.doctype;
		payload.reference_name = reference.value.name;
	}

	await consume((on, signal) => api.chat(payload, on, signal));
}

async function answer(text) {
	const pending = question.value;
	question.value = null;
	if (text !== "Approve" && text !== "Deny") {
		messages.value.push({ role: "user", content: text });
	}

	const run = runName.value;
	await consume((on, signal) => api.answer(run, { [pending.tool_call_id]: text }, on, signal));
}

function beginReply() {
	const reply = { role: "assistant", content: "", tools: [] };
	messages.value.push(reply);
	scroll();
	return reply;
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
		if (e.name !== "AbortError") failed = e.message || __("The assistant stopped unexpectedly.");
	} finally {
		if (inflight === controller) {
			inflight = null;
			busy.value = false;
			notice.value = null;
			if (failed) error.value = failed;
			// Nothing was said, so there is nothing to keep — and the attempt can simply be
			// made again. Once the assistant has spoken, resending would say it all twice.
			if (!reply.content && !reply.tools.length) {
				messages.value.splice(messages.value.indexOf(reply), 1);
				if (error.value) retryable.value = start;
			}
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
	reference.value =
		past.reference_doctype && past.reference_name
			? { doctype: past.reference_doctype, name: past.reference_name }
			: reference.value;
	messages.value = past.messages.map((message) => ({
		role: message.role,
		content: message.content,
		tools: (message.tools || []).map((name) => ({ name, done: true, error: null })),
	}));
	scroll();
}

// -- chrome --------------------------------------------------------------------------

function toggle() {
	open.value = !open.value;
}

function scroll() {
	nextTick(() => {
		if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
	});
}

function focusComposer() {
	nextTick(() => composer.value && composer.value.focus());
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

		<aside v-show="open" class="sai-panel">
			<header class="sai-head">
				<div class="sai-title">{{ __("Sales AI") }}</div>
				<div class="sai-head-actions">
					<button class="sai-icon" :title="__('Recent chats')" @click="toggleRecent">
						&#9776;
					</button>
					<button class="sai-icon" :title="__('New chat')" @click="newChat">+</button>
					<button class="sai-icon" :title="__('Close')" @click="toggle">&times;</button>
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
				<a :href="'/app/' + frappe.router.slug(reference.doctype) + '/' + encodeURIComponent(reference.name)">
					{{ reference.name }}
				</a>
			</div>

			<div ref="scroller" class="sai-body">
				<div v-if="!messages.length" class="sai-empty">
					{{ __("Ask about leads, opportunities, quotations or orders you can see.") }}
				</div>
				<ChatMessage v-for="(message, i) in messages" :key="i" :message="message" />
				<div v-if="busy" class="sai-thinking">
					{{ notice || __("Thinking") + "…" }}
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

			<footer class="sai-composer">
				<textarea
					ref="composer"
					v-model="draft"
					class="sai-textarea"
					rows="2"
					:placeholder="__('Ask a question')"
					:disabled="busy || !!question"
					@keydown.enter.exact.prevent="send"
				></textarea>
				<button
					class="sai-btn sai-btn-primary"
					:disabled="busy || !!question || !draft.trim()"
					@click="send"
				>
					{{ __("Send") }}
				</button>
			</footer>
		</aside>
	</div>
</template>
