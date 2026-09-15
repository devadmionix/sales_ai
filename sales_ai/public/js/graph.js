// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

// Draws a playbook as a picture. Frappe does not ship a diagram library, so this is plain
// SVG: one column of boxes in table order, with the fall-through arrows down the middle and
// anything that jumps — a branch, a loop going back, a loop finishing — routed through a
// gutter beside the column.
//
// The edges are not worked out here. They come from the server's `plan()`, which is the same
// thing the engine walks, so the picture cannot drift from what will actually run.

frappe.provide("sales_ai.graph");

// Wrapped, because this is a plain script rather than a module: the constants below would
// otherwise be shared with every other script on the desk.
(function () {
const NODE_W = 250;
const NODE_H = 46;
const GAP = 28;
const LANE = 22;
const PAD = 14;

const TONE = {
	Tool: "#2490ef",
	AI: "#7c3aed",
	Condition: "#f59e0b",
	Wait: "#64748b",
	Approval: "#e11d48",
	Loop: "#0ea5e9",
	Output: "#10b981",
};

const STATUS_FILL = {
	Done: "#ecfdf5",
	Skipped: "#f1f5f9",
	Refused: "#fff7ed",
	Failed: "#fef2f2",
};

sales_ai.graph.render = function (wrapper, data, options = {}) {
	const $wrapper = $(wrapper).empty();
	const nodes = data.nodes || [];
	if (!nodes.length) {
		$wrapper.html(`<div class="text-muted">${__("No steps yet.")}</div>`);
		return;
	}

	const index = {};
	nodes.forEach((node, i) => (index[node.key] = i));

	const trace = data.trace || [];
	const walked = path_taken(trace);
	const edges = collect_edges(nodes, index, walked.edges);
	const gutters = assign_lanes(edges);

	const left = gutters.left ? gutters.left * LANE + 8 : 0;
	const right = gutters.right ? gutters.right * LANE + 8 : 0;
	const column = PAD + left;
	const width = column + NODE_W + right + PAD;
	const height = PAD * 2 + nodes.length * NODE_H + (nodes.length - 1) * GAP;
	const y_of = (i) => PAD + i * (NODE_H + GAP);

	const parts = edges.map((edge) => draw_edge(edge, y_of, column));
	nodes.forEach((node, i) => {
		parts.push(
			draw_node(node, y_of(i), column, {
				status: walked.status[node.key],
				replay: trace.length > 0,
				cursor: data.cursor === node.key,
			})
		);
	});

	$wrapper.html(`
		<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}"
			style="max-width:${width}px; font-family:inherit">
			<defs>
				<marker id="sai-arrow" viewBox="0 0 8 8" refX="7" refY="4"
					markerWidth="6" markerHeight="6" orient="auto">
					<path d="M0,0 L8,4 L0,8 z" fill="#9ca3af"/>
				</marker>
				<marker id="sai-arrow-on" viewBox="0 0 8 8" refX="7" refY="4"
					markerWidth="6" markerHeight="6" orient="auto">
					<path d="M0,0 L8,4 L0,8 z" fill="#1f2937"/>
				</marker>
			</defs>
			${parts.join("")}
		</svg>
	`);

	if (options.on_click) {
		$wrapper
			.find("[data-step]")
			.css("cursor", "pointer")
			.on("click", function () {
				options.on_click($(this).attr("data-step"));
			});
	}
};

// -- what happened ---------------------------------------------------------------------

// The trace is the run in the order it actually went, so the edges it took are simply its
// consecutive pairs. A step that ran more than once inside a loop shows its latest outcome.
function path_taken(trace) {
	const status = {};
	const edges = new Set();
	trace.forEach((row, i) => {
		status[row.step_key] = row.status;
		const following = trace[i + 1];
		if (following) edges.add(`${row.step_key}->${following.step_key}`);
	});
	return { status, edges };
}

// -- edges -----------------------------------------------------------------------------

function collect_edges(nodes, index, taken) {
	const edges = [];

	const add = (node, to, label, dashed) => {
		if (!to || !(to in index)) return;
		const from_index = index[node.key];
		const to_index = index[to];
		edges.push({
			from_index,
			to_index,
			label,
			dashed,
			straight: to_index === from_index + 1,
			on: taken.has(`${node.key}->${to}`),
		});
	};

	for (const node of nodes) {
		if (node.type === "Condition") add(node, node.next, __("true"), false);
		else if (node.type === "Loop") add(node, node.next, __("each"), false);
		else add(node, node.next, "", false);

		if (node.on_false) {
			add(node, node.on_false, node.type === "Approval" ? __("denied") : __("false"), true);
		}
		if (node.after_loop) add(node, node.after_loop, __("done"), true);
	}
	return edges;
}

// A jump gets a lane of its own so two of them do not sit on top of each other. Forward
// jumps are routed down the right, anything going back up the left, which is where loops
// end up — so a loop reads as a ring beside the steps it repeats.
function assign_lanes(edges) {
	const occupied = { left: [], right: [] };

	for (const edge of edges) {
		if (edge.straight) continue;
		edge.side = edge.to_index > edge.from_index ? "right" : "left";

		const top = Math.min(edge.from_index, edge.to_index);
		const bottom = Math.max(edge.from_index, edge.to_index);
		const lanes = occupied[edge.side];

		edge.lane = lanes.findIndex(
			(spans) => !spans.some(([a, b]) => top <= b && a <= bottom)
		);
		if (edge.lane === -1) edge.lane = lanes.push([]) - 1;
		lanes[edge.lane].push([top, bottom]);
	}

	return { left: occupied.left.length, right: occupied.right.length };
}

function draw_edge(edge, y_of, column) {
	const stroke = edge.on ? "#1f2937" : "#9ca3af";
	const style = [
		`stroke:${stroke}`,
		`stroke-width:${edge.on ? 2 : 1.25}`,
		"fill:none",
		"stroke-linejoin:round",
		edge.dashed ? "stroke-dasharray:4 3" : "",
	].join(";");
	const marker = `url(#sai-arrow${edge.on ? "-on" : ""})`;

	let path;
	let label_x;
	let label_y;

	if (edge.straight) {
		const x = column + NODE_W / 2;
		path = `M ${x} ${y_of(edge.from_index) + NODE_H} V ${y_of(edge.to_index)}`;
		label_x = x + 6;
		label_y = y_of(edge.from_index) + NODE_H + GAP / 2 + 4;
	} else {
		const from_y = y_of(edge.from_index) + NODE_H / 2;
		const to_y = y_of(edge.to_index) + NODE_H / 2;
		const edge_x = edge.side === "right" ? column + NODE_W : column;
		const lane_x =
			edge.side === "right"
				? edge_x + 8 + edge.lane * LANE
				: edge_x - 8 - edge.lane * LANE;

		path = `M ${edge_x} ${from_y} H ${lane_x} V ${to_y} H ${edge_x}`;
		label_x = lane_x;
		label_y = (from_y + to_y) / 2 - 4;
	}

	const label = edge.label
		? `<text x="${label_x}" y="${label_y}" font-size="10" fill="${stroke}"
			text-anchor="${edge.straight ? "start" : "middle"}"
			paint-order="stroke" stroke="#fff" stroke-width="3"
			>${frappe.utils.escape_html(edge.label)}</text>`
		: "";

	return `<path d="${path}" style="${style}" marker-end="${marker}"/>${label}`;
}

function draw_node(node, y, x, state) {
	const tone = TONE[node.type] || "#6b7280";
	const fill = STATUS_FILL[state.status] || "#fff";
	// In a replay, a step with no trace row never ran. Fading it is what makes the path
	// the run actually took readable at a glance.
	const faded = state.replay && !state.status && !state.cursor;

	const badge = state.cursor ? __("next") : state.status || "";
	const caption = [node.type, node.key !== node.label ? node.key : ""]
		.filter(Boolean)
		.join(" · ");

	return `
		<g data-step="${frappe.utils.escape_html(node.key)}" opacity="${faded ? 0.4 : 1}">
			<rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="6"
				fill="${fill}" stroke="${tone}" stroke-width="1.5"
				${state.cursor ? 'stroke-dasharray="5 3"' : ""}/>
			<rect x="${x}" y="${y}" width="4" height="${NODE_H}" fill="${tone}"/>
			<text x="${x + 14}" y="${y + 20}" font-size="12.5" font-weight="600" fill="#1f2937"
				>${frappe.utils.escape_html(truncate(node.label, 30))}</text>
			<text x="${x + 14}" y="${y + 36}" font-size="10.5" fill="#6b7280"
				>${frappe.utils.escape_html(caption)}</text>
			${
				badge
					? `<text x="${x + NODE_W - 12}" y="${y + 20}" font-size="10" fill="${tone}"
						text-anchor="end">${frappe.utils.escape_html(badge)}</text>`
					: ""
			}
		</g>`;
}

function truncate(text, limit) {
	text = text || "";
	return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
}
})();
