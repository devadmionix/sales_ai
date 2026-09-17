// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

// Mounts the assistant panel into the desk. It lives in its own element at the end of
// <body>, so it never competes with a page's own layout.

import { createApp } from "vue";
import ChatPanel from "./panel/ChatPanel.vue";

frappe.provide("frappe.sales_ai");

function mount() {
	if (frappe.sales_ai.panel) return;

	const wrapper = document.createElement("div");
	document.body.appendChild(wrapper);

	const app = createApp(ChatPanel);
	SetVueGlobals(app);
	frappe.sales_ai.panel = app.mount(wrapper);

	frappe.ui.keys.add_shortcut({
		shortcut: "ctrl+shift+a",
		action: () => frappe.sales_ai.panel.toggle(),
		description: __("Toggle Sales AI"),
	});

	watch_background_runs();
}

// An unattended run has no window to report into, so it says what it is doing here. The
// approval itself goes to the notification bell and the user's to-do list, which survive
// a closed tab; this is only the nudge.
function watch_background_runs() {
	frappe.realtime.on("sales_ai:run", (data) => {
		if (data.state === "paused") {
			frappe.show_alert(
				{
					message: __("Sales AI needs your approval"),
					indicator: "orange",
				},
				10
			);
		} else if (data.state === "completed") {
			frappe.show_alert({ message: __("Sales AI finished a task"), indicator: "green" }, 5);
		}
	});
}

// Replace the Sales AI workspace icon with a colorful AI-style icon.
const SAI_ICON_SVG = `<svg viewBox="0 0 56 56" width="56" height="56" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="sai-g1" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#7C3AED"/>
      <stop offset="50%" stop-color="#EC4899"/>
      <stop offset="100%" stop-color="#F59E0B"/>
    </linearGradient>
  </defs>
  <rect width="56" height="56" rx="13" fill="url(#sai-g1)"/>
  <path d="M14,38 L22,30 L28,33 L36,22 L42,18" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <polyline points="38,16 42,18 40,22" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M38,32 L39.5,27 L41,32 L46,33.5 L41,35 L39.5,40 L38,35 L33,33.5 Z" fill="white" opacity="0.95"/>
  <path d="M17,19 L18,16.5 L19,19 L21.5,20 L19,21 L18,23.5 L17,21 L14.5,20 Z" fill="white" opacity="0.75"/>
</svg>`;

function replaceSalesAIIcon() {
	// Desktop grid icons (home page): .desktop-icon[data-id="Sales AI"]
	document.querySelectorAll('.desktop-icon[data-id="Sales AI"]').forEach((el) => {
		const container = el.querySelector(".icon-container");
		if (!container || container.dataset.saiDone) return;
		container.dataset.saiDone = "1";
		container.style.background = "none";
		container.style.border = "none";
		container.style.boxShadow = "none";
		container.style.padding = "0";
		container.innerHTML = SAI_ICON_SVG;
	});

	// Sidebar items: .sidebar-item-container[item-name="Sales AI"]
	document.querySelectorAll('.sidebar-item-container[item-name="Sales AI"]').forEach((el) => {
		const iconSpan = el.querySelector(".sidebar-item-icon");
		if (!iconSpan || iconSpan.dataset.saiDone) return;
		iconSpan.dataset.saiDone = "1";
		iconSpan.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" xmlns="http://www.w3.org/2000/svg">
			<defs><linearGradient id="sai-sb" x1="0%" y1="0%" x2="100%" y2="100%">
				<stop offset="0%" stop-color="#7C3AED"/><stop offset="50%" stop-color="#EC4899"/><stop offset="100%" stop-color="#F59E0B"/>
			</linearGradient></defs>
			<rect width="24" height="24" rx="5" fill="url(#sai-sb)"/>
			<path d="M4,17 L8,13 L11,14.5 L16,8 L20,6" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
			<path d="M16,10 L17,7 L18,10 L21,11 L18,12 L17,15 L16,12 L13,11 Z" fill="white" opacity="0.9"/>
		</svg>`;
	});
}

$(document).on("app_ready", () => {
	if (!(frappe.boot.sales_ai && frappe.boot.sales_ai.enabled)) return;

	mount();

	// "Ask AI" button visible on every page; clicking it toggles the chat panel.
	const panel = frappe.sales_ai.panel;
	if (!panel) return;

	const askBtn = document.createElement("button");
	askBtn.className = "sai-ask-btn";
	askBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" style="vertical-align:middle;margin-right:6px;"><path fill="currentColor" d="M12 2.5 13.8 8 19 9.8 13.8 11.6 12 17l-1.8-5.4L5 9.8 10.2 8 12 2.5Z"/></svg>' + __("Ask AI");
	askBtn.onclick = () => panel.toggle();
	document.body.appendChild(askBtn);

	// Hide the button while the panel is open. Watch the `.sai-panel` element's
	// style attribute — Vue's v-show toggles `display` on it.
	const syncBtn = () => {
		const panelEl = document.querySelector(".sai-panel");
		askBtn.style.display = panelEl && panelEl.style.display !== "none" ? "none" : "";
	};
	syncBtn();
	setInterval(syncBtn, 300);

	// Replace the workspace icon on the desk. The desktop grid loads its icons
	// asynchronously, so retry a few times after each route change.
	const runIconReplace = () => {
		replaceSalesAIIcon();
		setTimeout(replaceSalesAIIcon, 500);
		setTimeout(replaceSalesAIIcon, 1500);
		setTimeout(replaceSalesAIIcon, 3000);
	};
	runIconReplace();
	frappe.router.on("change", () => setTimeout(runIconReplace, 200));
});
