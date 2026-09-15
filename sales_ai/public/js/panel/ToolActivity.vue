<!-- Copyright (c) 2026, Admionix and contributors -->
<!-- For license information, please see license.txt -->

<script setup>
defineProps({
	tools: { type: Array, required: true },
});

// Tool names are snake_case identifiers; the user should read them as words.
const label = (name) => name.replace(/_/g, " ");
</script>

<template>
	<div class="sai-tools">
		<span
			v-for="tool in tools"
			:key="tool.id || tool.name"
			class="sai-tool"
			:class="{ 'sai-tool-running': !tool.done, 'sai-tool-failed': tool.error }"
			:title="tool.error || ''"
		>
			<span class="sai-tool-dot"></span>{{ label(tool.name) }}
		</span>
	</div>
</template>
