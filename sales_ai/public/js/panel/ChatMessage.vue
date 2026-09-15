<!-- Copyright (c) 2026, Admionix and contributors -->
<!-- For license information, please see license.txt -->

<script setup>
import { computed } from "vue";
import ToolActivity from "./ToolActivity.vue";

const props = defineProps({
	message: { type: Object, required: true },
});

// frappe.markdown() sanitises its output against a tag whitelist, so the assistant
// cannot inject markup by writing it into its reply.
const rendered = computed(() => frappe.markdown(props.message.content || ""));
</script>

<template>
	<div class="sai-msg" :class="'sai-msg-' + message.role">
		<ToolActivity v-if="message.tools && message.tools.length" :tools="message.tools" />
		<div v-if="message.role === 'user'" class="sai-bubble">{{ message.content }}</div>
		<div v-else-if="message.content" class="sai-bubble sai-markdown" v-html="rendered"></div>
	</div>
</template>
