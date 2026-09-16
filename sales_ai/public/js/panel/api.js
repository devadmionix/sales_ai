// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

// Talking to sales_ai.api over Server-Sent Events.
//
// EventSource cannot POST, so the stream is read off a plain fetch body. Frappe's own
// frappe.call() buffers the whole response, which would defeat the point of streaming.

const CHAT = "/api/method/sales_ai.api.chat";
const ANSWER = "/api/method/sales_ai.api.answer";

// Send a message and invoke `on(event, data)` for every frame until the stream ends.
export function chat(payload, on, signal) {
	return stream(CHAT, payload, on, signal);
}

// Reply to a paused run's approval question.
export function answer(run, answers, on, signal) {
	return stream(ANSWER, { run, answers }, on, signal);
}

export async function history(session) {
	return call("sales_ai.api.history", { session });
}

export async function sessions(limit) {
	return call("sales_ai.api.sessions", { limit });
}

async function call(method, args) {
	const response = await frappe.call({ method, args });
	return response.message;
}

// How long a stream may say nothing before it is presumed dead. Generous, because a model
// call with a slow tool behind it can legitimately be quiet for a while, and cutting off a
// working answer is worse than waiting. The point is only that "Thinking…" cannot last for
// ever: a connection dropped by a proxy or a laptop lid looks exactly like a slow model
// from here, and without this the panel sits on it until the tab is closed.
const IDLE_TIMEOUT = 120000;

async function stream(url, payload, on, signal) {
	const response = await fetch(url, {
		method: "POST",
		signal,
		headers: {
			"Content-Type": "application/json",
			Accept: "text/event-stream",
			"X-Frappe-CSRF-Token": frappe.csrf_token,
		},
		body: JSON.stringify(payload),
	});

	// An auth or validation failure never reaches the generator, so it arrives as JSON.
	if (!response.ok || !(response.headers.get("Content-Type") || "").includes("text/event-stream")) {
		on("error", { message: await errorMessage(response) });
		return;
	}

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";

	try {
		for (;;) {
			const { value, done } = await withIdleTimeout(reader.read(), reader);
			if (done) break;
			buffer += decoder.decode(value, { stream: true });

			// Frames are separated by a blank line; the tail is an incomplete frame.
			const frames = buffer.split("\n\n");
			buffer = frames.pop();
			for (const frame of frames) {
				const parsed = parse(frame);
				// A frame we cannot read is a fault, not a thing to skip quietly. Dropping
				// it loses whatever it said — which may have been the answer.
				if (parsed) on(parsed.event, parsed.data);
				else if (frame.trim()) on("error", { message: __("The assistant sent something unreadable.") });
			}
		}
	} finally {
		// Aborting mid-stream leaves the body open otherwise, and the server goes on
		// generating into a socket nobody is reading.
		reader.cancel().catch(() => {});
	}
}

function withIdleTimeout(read, reader) {
	let timer;
	const expiry = new Promise((_resolve, reject) => {
		timer = setTimeout(() => {
			reader.cancel().catch(() => {});
			reject(new Error(__("The assistant stopped responding.")));
		}, IDLE_TIMEOUT);
	});
	return Promise.race([read, expiry]).finally(() => clearTimeout(timer));
}

function parse(frame) {
	let event = "message";
	const data = [];
	for (const line of frame.split("\n")) {
		if (line.startsWith("event:")) event = line.slice(6).trim();
		else if (line.startsWith("data:")) data.push(line.slice(5).trim());
	}
	if (!data.length) return null;
	try {
		return { event, data: JSON.parse(data.join("\n")) };
	} catch {
		return null;
	}
}

async function errorMessage(response) {
	try {
		const body = await response.json();
		const messages = JSON.parse(body._server_messages || "[]");
		if (messages.length) return JSON.parse(messages[0]).message;
		return body.exception || body.message || response.statusText;
	} catch {
		return __("Could not reach the assistant.");
	}
}
