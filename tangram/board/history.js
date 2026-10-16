import { VISUAL_LIMITS } from "./limits.js";
import { utf8Bytes } from "./codec.js";
export function createHistoryRecorder(now = Date.now) {
    const startedMs = now();
    const startedAt = new Date(startedMs).toISOString();
    const events = [];
    return {
        record(input) {
            const current = now();
            const event = {
                seq: events.length + 1,
                client_at: new Date(current).toISOString(),
                elapsed_ms: Math.max(0, current - startedMs),
                ...structuredClone(input),
            };
            events.push(event);
            return structuredClone(event);
        },
        snapshot() {
            const included = [];
            const build = (candidateEvents) => ({
                schema_version: 1,
                started_at: startedAt,
                total_events: events.length,
                included_events: candidateEvents.length,
                omitted_events: events.length - candidateEvents.length,
                truncated: candidateEvents.length !== events.length,
                events: candidateEvents,
            });
            for (const event of events) {
                if (included.length >= VISUAL_LIMITS.historyEvents)
                    break;
                const candidate = [...included, event];
                if (utf8Bytes(JSON.stringify(build(candidate))) > VISUAL_LIMITS.historyBytes)
                    break;
                included.push(event);
            }
            return structuredClone(build(included));
        },
        all: () => structuredClone(events),
        toJsonl: () => events.map((event) => JSON.stringify(event)).join("\n") + (events.length ? "\n" : ""),
    };
}
