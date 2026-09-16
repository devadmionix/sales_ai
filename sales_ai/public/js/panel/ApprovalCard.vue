<!-- Copyright (c) 2026, Admionix and contributors -->
<!-- For license information, please see license.txt -->

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
	question: { type: Object, required: true },
	busy: { type: Boolean, default: false },
});
const emit = defineEmits(["reply"]);

const other = ref("");
const args = computed(() => JSON.stringify(props.question.arguments || {}, null, 2));

// What the call would come to, worked out server-side before anyone approves it.
const preview = computed(() => props.question.preview || null);
const lines = computed(() => preview.value?.lines || []);
const totals = computed(() => preview.value?.totals || {});
const currency = computed(() => preview.value?.currency);

// The grand total is the number being approved. Fall back down the chain because a
// quote without tax has no rounded_total, and one without lines has no total at all.
const payable = computed(
	() => totals.value.rounded_total ?? totals.value.grand_total ?? totals.value.total,
);
const tax = computed(() => totals.value.total_taxes_and_charges);

// How consequential the tool is. Only the bands worth pausing over are labelled — a badge
// on every card is a badge on none of them, and most approvals are routine.
const risk = computed(() => props.question.risk);
const riskLabel = computed(
	() =>
		({
			medium: __("Medium risk"),
			high: __("High risk"),
			critical: __("Critical"),
		})[risk.value] || "",
);

function money(value) {
	if (value === undefined || value === null) return "";
	return format_currency(value, currency.value);
}

function redirect() {
	const text = other.value.trim();
	if (!text) return;
	other.value = "";
	emit("reply", text);
}
</script>

<template>
	<div class="sai-approval">
		<div class="sai-approval-prompt">
			{{ question.prompt }}
			<span v-if="riskLabel" class="sai-risk" :class="`sai-risk-${risk}`">{{ riskLabel }}</span>
		</div>

		<div v-if="preview?.error" class="sai-approval-warning">{{ preview.error }}</div>

		<table v-else-if="lines.length" class="sai-approval-lines">
			<tbody>
				<tr v-for="(line, index) in lines" :key="index">
					<td>
						{{ line.item_name || line.item_code }}
						<span class="sai-muted">{{ line.qty }} {{ line.uom }}</span>
					</td>
					<td class="sai-num">
						<span v-if="line.discount_percentage" class="sai-was">{{
							money(line.price_list_rate)
						}}</span>
						{{ money(line.rate) }}
					</td>
					<td class="sai-num">{{ money(line.amount) }}</td>
				</tr>
			</tbody>
			<tfoot>
				<tr v-if="tax">
					<td colspan="2" class="sai-muted">{{ __("Tax") }}</td>
					<td class="sai-num sai-muted">{{ money(tax) }}</td>
				</tr>
				<tr class="sai-approval-total">
					<td colspan="2">{{ preview.name || preview.customer || __("Total") }}</td>
					<td class="sai-num">{{ money(payable) }}</td>
				</tr>
			</tfoot>
		</table>

		<details class="sai-approval-details">
			<summary>{{ __("Details") }}</summary>
			<pre class="sai-approval-args">{{ args }}</pre>
		</details>
		<div class="sai-approval-actions">
			<button
				v-for="option in question.options"
				:key="option"
				class="sai-btn"
				:class="option === 'Deny' ? 'sai-btn-danger' : 'sai-btn-primary'"
				:disabled="busy"
				@click="emit('reply', option)"
			>
				{{ __(option) }}
			</button>
		</div>
		<div v-if="question.allow_other" class="sai-approval-other">
			<input
				v-model="other"
				class="sai-input"
				:placeholder="__('Or tell it what to do instead')"
				:disabled="busy"
				@keydown.enter="redirect"
			/>
			<button class="sai-btn" :disabled="busy || !other.trim()" @click="redirect">
				{{ __("Send") }}
			</button>
		</div>
	</div>
</template>
