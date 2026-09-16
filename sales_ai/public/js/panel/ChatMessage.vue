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

// One letter is enough to say whose message it is, and it costs no request. A real avatar
// would mean fetching the user's image for every message on screen.
const initial = computed(() =>
	((frappe.session.user_fullname || frappe.session.user || "?")[0] || "?").toUpperCase()
);

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
	<!-- The two roles are laid out differently on purpose. What you said is a short pill
	     against the right edge with your initial beside it; what the assistant said is
	     ordinary prose filling the column, because it is the thing being read. -->
	<div v-if="mine" class="sai-turn sai-turn-user">
		<div v-if="message.at" class="sai-at">{{ message.at }}</div>
		<div class="sai-said">
			<div class="sai-pill">{{ message.content }}</div>
			<div class="sai-avatar">{{ initial }}</div>
		</div>
	</div>

	<div v-else class="sai-turn">
		<ToolActivity v-if="message.tools && message.tools.length" :tools="message.tools" />
		<div v-if="message.content" class="sai-markdown" v-html="rendered"></div>
		<div v-if="message.content" class="sai-foot">
			<span v-if="message.at" class="sai-at">{{ message.at }}</span>
			<button class="sai-copy" :title="__('Copy')" @click="copy">
				{{ copied ? __("Copied") : __("Copy") }}
			</button>
		</div>
	</div>
</template>
