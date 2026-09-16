<!-- Copyright (c) 2026, Admionix and contributors -->
<!-- For license information, please see license.txt -->

<script setup>
import { computed, ref } from "vue";
import ToolActivity from "./ToolActivity.vue";

const props = defineProps({
	message: { type: Object, required: true },
});

const mine = computed(() => props.message.role === "user");

// frappe.markdown() sanitises its output against a tag whitelist, so the assistant
// cannot inject markup by writing it into its reply.
const rendered = computed(() => frappe.markdown(props.message.content || ""));

const who = computed(() =>
	mine.value ? frappe.session.user_fullname || __("You") : __("Sales AI")
);

// One letter is enough to tell the two apart at a glance, and it costs no request. A full
// avatar would mean fetching the user's image for every message on screen.
const initial = computed(() => (who.value[0] || "?").toUpperCase());

const copied = ref(false);

function copy() {
	// The markdown source, not the rendered HTML: what gets pasted into a note or an email
	// should be the text the assistant wrote.
	navigator.clipboard.writeText(props.message.content || "").then(() => {
		copied.value = true;
		setTimeout(() => (copied.value = false), 1500);
	});
}
</script>

<template>
	<div class="sai-msg" :class="'sai-msg-' + message.role">
		<div class="sai-avatar" :class="mine ? 'sai-avatar-user' : 'sai-avatar-bot'">
			{{ mine ? initial : "" }}
			<svg v-if="!mine" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
				<path
					fill="none"
					stroke="currentColor"
					stroke-width="1.8"
					stroke-linecap="round"
					stroke-linejoin="round"
					d="M12 3v3M7.5 6h9A2.5 2.5 0 0 1 19 8.5v7a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 5 15.5v-7A2.5 2.5 0 0 1 7.5 6ZM9.5 11v1.5M14.5 11v1.5"
				/>
			</svg>
		</div>

		<div class="sai-msg-body">
			<div class="sai-meta">
				<span class="sai-who">{{ who }}</span>
				<span v-if="message.at" class="sai-at">{{ message.at }}</span>
				<button
					v-if="!mine && message.content"
					class="sai-copy"
					:title="__('Copy')"
					@click="copy"
				>
					{{ copied ? __("Copied") : __("Copy") }}
				</button>
			</div>
			<ToolActivity v-if="message.tools && message.tools.length" :tools="message.tools" />
			<div v-if="mine" class="sai-bubble">{{ message.content }}</div>
			<div
				v-else-if="message.content"
				class="sai-bubble sai-markdown"
				v-html="rendered"
			></div>
		</div>
	</div>
</template>
