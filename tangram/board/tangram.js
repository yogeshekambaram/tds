import { TANGRAM_CONFIG, TANGRAM_INITIAL_POSES, applyTangramPose, tangramAdapter, tangramEnvelope, tangramPolygons, } from "../../games/tangram/adapter.js";
import { algValue } from "../../games/tangram/algebraic.js";
import { bytesToBase64Url } from "../codec.js";
import { createHistoryRecorder } from "../history.js";
import { computeMatchKey } from "../kernel.js";
import { createVisualRuntime } from "../runtime.js";
import { decodeStateFragment, encodeStateFragment } from "../state-link.js";
import { downloadDataUrl, downloadText, svgToPngDataUrl } from "./image-export.js";
import { registerWebMcpTools } from "./webmcp.js";
const SVG_NS = "http://www.w3.org/2000/svg";
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const HUMAN_SNAP_TICKS = 4; // 1/4 board unit; exact agent state still supports 1/16.
const COLORS = {
    "large-a": "#4f86c6",
    "large-b": "#5aa469",
    medium: "#c46d5e",
    "small-a": "#d8a03d",
    "small-b": "#8f6bb3",
    square: "#4aa6a6",
    parallelogram: "#c45b8e",
};
const initialState = tangramAdapter.parseCandidate(tangramEnvelope(TANGRAM_INITIAL_POSES), TANGRAM_CONFIG);
const runtime = createVisualRuntime({
    adapter: tangramAdapter,
    config: TANGRAM_CONFIG,
    initialState,
    applyAction: (state, action, config) => applyTangramPose(state, action, config),
});
const history = createHistoryRecorder();
let borders = true;
let renderSequence = 0;
let latestAttestationRequest = 0;
function requiredElement(id) {
    const element = document.getElementById(id);
    if (!element)
        throw new Error("missing #" + id);
    return element;
}
const board = requiredElement("board");
const status = requiredElement("status");
const revision = requiredElement("revision");
const matchKey = requiredElement("match-key");
const linkStatus = requiredElement("link-status");
const historyStatus = requiredElement("history-status");
const webMcpStatus = requiredElement("webmcp-status");
const receiptStatus = requiredElement("receipt-status");
const pieceSelect = requiredElement("piece");
const bordersButton = requiredElement("borders");
const emailInput = requiredElement("email");
const stateText = requiredElement("state-text");
function poseById(state, id) {
    const pose = state.pieces.find((piece) => piece.id === id);
    if (!pose)
        throw new Error("piece " + id + " is unavailable");
    return pose;
}
function selectedPose(state) {
    return poseById(state, pieceSelect.value);
}
function syncControls() {
    bordersButton.setAttribute("aria-pressed", String(borders));
    bordersButton.textContent = borders ? "Labels on" : "Labels off";
    const disabled = !pieceSelect.value;
    for (const id of ["rotate-left", "rotate-right", "flip"]) {
        requiredElement(id).disabled = disabled;
    }
}
function makeSvg(name) {
    return document.createElementNS(SVG_NS, name);
}
function updateSelectedPieceHighlight() {
    for (const element of board.querySelectorAll("[data-piece]")) {
        const selected = borders && element.dataset.piece === pieceSelect.value;
        element.classList.toggle("selected-piece", selected);
    }
}
function svgPoints(vertices) {
    return vertices.map((point) => {
        const x = algValue(point.x);
        const y = TANGRAM_CONFIG.board.height - algValue(point.y);
        return x + "," + y;
    }).join(" ");
}
function renderBoard(state) {
    board.replaceChildren();
    board.style.touchAction = "none";
    const background = makeSvg("rect");
    background.setAttribute("x", "0");
    background.setAttribute("y", "0");
    background.setAttribute("width", String(TANGRAM_CONFIG.board.width));
    background.setAttribute("height", String(TANGRAM_CONFIG.board.height));
    background.setAttribute("fill", "#0d120f");
    board.append(background);
    const selectedId = pieceSelect.value;
    const polygons = tangramPolygons(state, TANGRAM_CONFIG)
        .sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId));
    for (const polygon of polygons) {
        const shape = makeSvg("polygon");
        shape.setAttribute("points", svgPoints(polygon.vertices));
        const fill = borders ? (COLORS[polygon.id] ?? "#888") : "#d8dfda";
        shape.setAttribute("fill", fill);
        shape.setAttribute("stroke", borders ? "#f4fff7" : fill);
        shape.setAttribute("stroke-width", borders ? "1" : "1.5");
        shape.setAttribute("stroke-linejoin", "miter");
        shape.setAttribute("vector-effect", "non-scaling-stroke");
        shape.setAttribute("data-piece", polygon.id);
        if (borders && polygon.id === selectedId)
            shape.classList.add("selected-piece");
        board.append(shape);
    }
}
function currentFragment() {
    try {
        return encodeStateFragment(runtime.serialize(), { borders });
    }
    catch {
        return null;
    }
}
function updateMeta() {
    const fragment = currentFragment();
    linkStatus.textContent = !fragment
        ? "State exceeds URL budget; download the state file"
        : location.hash.slice(1) === fragment
            ? "Share link matches current state"
            : "Unsaved edits differ from share link";
    const snapshot = history.snapshot();
    historyStatus.textContent = snapshot.total_events + " local events" +
        (snapshot.truncated ? " (" + snapshot.omitted_events + " omitted from signed snapshot)" : "");
}
async function render(snapshot) {
    const sequence = ++renderSequence;
    renderBoard(snapshot.state);
    revision.textContent = String(snapshot.revision);
    syncControls();
    updateMeta();
    if (snapshot.validation.invalid) {
        const codes = snapshot.validation.errors.map((error) => error.code + (error.pieces?.length ? ":" + error.pieces.join("+") : "")).join(", ");
        status.textContent = "Invalid: " + codes;
        status.dataset.invalid = "true";
        matchKey.textContent = "—";
        return;
    }
    status.textContent = "Valid candidate";
    status.dataset.invalid = "false";
    const key = await computeMatchKey(tangramAdapter, snapshot.state, TANGRAM_CONFIG);
    if (sequence === renderSequence)
        matchKey.textContent = key;
}
function record(source, action, revisionValue, ok, detail, error) {
    history.record({
        source,
        action,
        revision: revisionValue,
        ok,
        ...(detail === undefined ? {} : { detail }),
        ...(error === undefined ? {} : { error }),
    });
    updateMeta();
}
function mutate(source, action, detail, operation) {
    try {
        const snapshot = operation();
        record(source, action, snapshot.revision, true, detail);
        void render(snapshot);
        return { ok: true, snapshot };
    }
    catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const current = runtime.snapshot();
        record(source, action, current.revision, false, detail, message);
        status.textContent = message;
        status.dataset.invalid = "true";
        return { ok: false, error: message, revision: current.revision };
    }
}
function writeFragment(fragment) {
    window.history.replaceState(null, "", location.pathname + location.search + "#" + fragment);
}
function saveShareLink(source) {
    try {
        const fragment = encodeStateFragment(runtime.serialize(), { borders });
        writeFragment(fragment);
        record(source, "save_share_link", runtime.snapshot().revision, true);
        updateMeta();
        return { ok: true, url: location.href };
    }
    catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        record(source, "save_share_link", runtime.snapshot().revision, false, undefined, message);
        return { ok: false, error: message };
    }
}
function saveCapturedLink(stateBytes) {
    writeFragment(encodeStateFragment(stateBytes, { borders }));
    updateMeta();
}
async function exportImage(download) {
    const dataUrl = await svgToPngDataUrl(board, 2);
    if (download)
        downloadDataUrl(dataUrl, "tangram-candidate.png");
    return { mime: "image/png", data_url: dataUrl };
}
async function requestAttestation(email, source, signal) {
    const capturedRevision = runtime.snapshot().revision;
    const capturedState = runtime.serialize();
    saveCapturedLink(capturedState);
    record(source, "attest_request", capturedRevision, true, { include_history: true });
    const requestNumber = ++latestAttestationRequest;
    receiptStatus.textContent = "Signing revision " + capturedRevision + "…";
    const response = await fetch("/api/visual/attest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            email,
            game: TANGRAM_CONFIG.game,
            game_version: TANGRAM_CONFIG.game_version,
            config_id: TANGRAM_CONFIG.config_id,
            state_version: TANGRAM_CONFIG.state_version,
            equivalence_version: TANGRAM_CONFIG.equivalence_version,
            state: bytesToBase64Url(capturedState),
            client_report: history.snapshot(),
        }),
        signal,
    });
    const data = await response.json();
    if (!response.ok || !data.receipt)
        throw new Error(data.message ?? data.error ?? "attestation failed");
    const currentRevision = runtime.snapshot().revision;
    const stale = currentRevision !== capturedRevision;
    const superseded = requestNumber !== latestAttestationRequest;
    if (!superseded) {
        receiptStatus.textContent = stale
            ? "Signed revision " + capturedRevision + "; board is now revision " + currentRevision
            : "Signed revision " + capturedRevision;
    }
    return {
        ok: true,
        captured_revision: capturedRevision,
        current_revision: currentRevision,
        stale,
        superseded,
        invalid: data.invalid,
        errors: data.errors,
        match_key: data.match_key,
        received_at: data.received_at,
        receipt: data.receipt,
    };
}
function restoreHash() {
    if (!location.hash)
        return;
    try {
        const decoded = decodeStateFragment(location.hash);
        if (!decoded)
            return;
        runtime.importEncoded(decoded.state);
        borders = decoded.presentation.borders ?? true;
        record("import", "restore_state_link", runtime.snapshot().revision, true);
    }
    catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        record("import", "restore_state_link", runtime.snapshot().revision, false, undefined, message);
        status.textContent = "Could not restore URL state: " + message;
    }
}
function setBorders(value, source) {
    borders = value;
    record(source, "set_borders", runtime.snapshot().revision, true, { borders });
    void render(runtime.snapshot());
    return { ok: true, borders, revision: runtime.snapshot().revision };
}
function worldPoint(event) {
    const point = board.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const matrix = board.getScreenCTM();
    if (!matrix)
        throw new Error("SVG coordinate transform unavailable");
    const transformed = point.matrixTransform(matrix.inverse());
    return [transformed.x, TANGRAM_CONFIG.board.height - transformed.y];
}
function humanSnappedTicks(units) {
    return Math.round(units * 16 / HUMAN_SNAP_TICKS) * HUMAN_SNAP_TICKS;
}
function sameTangramPose(a, b) {
    return a.id === b.id &&
        a.x === b.x &&
        a.y === b.y &&
        a.orientation === b.orientation &&
        a.reflected === b.reflected;
}
function stateWithPreview(state, preview) {
    return {
        pieces: state.pieces.map((pose) => pose.id === preview.id ? preview : pose),
    };
}
let drag = null;
board.addEventListener("pointerdown", (event) => {
    if (!(event.target instanceof SVGElement))
        return;
    const pieceId = event.target.dataset.piece;
    if (!pieceId) {
        pieceSelect.value = "";
        syncControls();
        renderBoard(runtime.snapshot().state);
        return;
    }
    pieceSelect.value = pieceId;
    syncControls();
    updateSelectedPieceHighlight();
    const snapshot = runtime.snapshot();
    const pose = poseById(snapshot.state, pieceId);
    const [x, y] = worldPoint(event);
    drag = {
        pieceId,
        pointerId: event.pointerId,
        dx: x - pose.x / 16,
        dy: y - pose.y / 16,
        preview: pose,
    };
});
window.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId)
        return;
    const [x, y] = worldPoint(event);
    const current = poseById(runtime.snapshot().state, drag.pieceId);
    const next = {
        ...current,
        x: humanSnappedTicks(x - drag.dx),
        y: humanSnappedTicks(y - drag.dy),
    };
    if (sameTangramPose(next, drag.preview))
        return;
    drag.preview = next;
    renderBoard(stateWithPreview(runtime.snapshot().state, next));
});
window.addEventListener("pointerup", (event) => {
    if (!drag || drag.pointerId !== event.pointerId)
        return;
    const active = drag;
    drag = null;
    const current = poseById(runtime.snapshot().state, active.pieceId);
    if (sameTangramPose(current, active.preview))
        return;
    mutate("manual", "set_pose", active.preview, () => runtime.dispatch(active.preview));
});
window.addEventListener("pointercancel", () => {
    drag = null;
    renderBoard(runtime.snapshot().state);
});
board.addEventListener("dblclick", (event) => {
    if (!(event.target instanceof SVGElement))
        return;
    const pieceId = event.target.dataset.piece;
    if (!pieceId)
        return;
    event.preventDefault();
    pieceSelect.value = pieceId;
    const pose = poseById(runtime.snapshot().state, pieceId);
    const next = {
        ...pose,
        orientation: ((pose.orientation + 1) % 8),
    };
    mutate("manual", "set_pose", next, () => runtime.dispatch(next));
});
restoreHash();
async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return;
    }
    catch {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.append(textarea);
        textarea.select();
        const copied = document.execCommand("copy");
        textarea.remove();
        if (!copied)
            throw new Error("Could not copy the signed payload. Check browser clipboard permissions.");
    }
}
async function copySignedPayload(result) {
    const receipt = typeof result.receipt === "string" ? result.receipt : "";
    if (!receipt)
        throw new Error("The server did not return a signed payload.");
    await copyText(receipt);
    const copiedRevision = Number(result.captured_revision);
    const currentRevision = Number(result.current_revision);
    receiptStatus.textContent = copiedRevision === currentRevision
        ? "Copied signed payload for revision " + copiedRevision + "."
        : "Copied signed payload for revision " + copiedRevision + "; board is now revision " + currentRevision + ".";
}
requiredElement("rotate-left").addEventListener("click", () => {
    const pose = selectedPose(runtime.snapshot().state);
    const next = { ...pose, orientation: ((pose.orientation + 7) % 8) };
    mutate("manual", "set_pose", next, () => runtime.dispatch(next));
});
requiredElement("rotate-right").addEventListener("click", () => {
    const pose = selectedPose(runtime.snapshot().state);
    const next = { ...pose, orientation: ((pose.orientation + 1) % 8) };
    mutate("manual", "set_pose", next, () => runtime.dispatch(next));
});
requiredElement("flip").addEventListener("click", () => {
    const pose = selectedPose(runtime.snapshot().state);
    const next = { ...pose, reflected: !pose.reflected };
    mutate("manual", "set_pose", next, () => runtime.dispatch(next));
});
requiredElement("undo").addEventListener("click", () => mutate("manual", "undo", undefined, () => runtime.undo()));
requiredElement("redo").addEventListener("click", () => mutate("manual", "redo", undefined, () => runtime.redo()));
requiredElement("reset").addEventListener("click", () => mutate("manual", "reset", undefined, () => runtime.reset()));
requiredElement("serialize").addEventListener("click", () => {
    stateText.value = decoder.decode(runtime.serialize());
});
requiredElement("import-state").addEventListener("click", () => mutate("import", "import_state", undefined, () => runtime.importEncoded(encoder.encode(stateText.value))));
requiredElement("save-share").addEventListener("click", () => {
    const result = saveShareLink("manual");
    status.textContent = result.ok ? "Share link updated" : String(result.error);
});
requiredElement("download-history").addEventListener("click", () => downloadText(history.toJsonl(), "tangram-history.jsonl", "application/x-ndjson"));
requiredElement("export-image").addEventListener("click", () => {
    exportImage(true).catch((error) => { status.textContent = error instanceof Error ? error.message : String(error); });
});
requiredElement("attest").addEventListener("click", async (event) => {
    if (!emailInput.reportValidity())
        return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
        const result = await requestAttestation(emailInput.value, "manual");
        await copySignedPayload(result);
    }
    catch (error) {
        receiptStatus.textContent = error instanceof Error ? error.message : String(error);
    }
    finally {
        button.disabled = false;
    }
});
pieceSelect.addEventListener("change", () => {
    syncControls();
    renderBoard(runtime.snapshot().state);
});
bordersButton.addEventListener("click", () => setBorders(!borders, "manual"));
document.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement)
        return;
    if (!pieceSelect.value)
        return;
    const pose = selectedPose(runtime.snapshot().state);
    const key = event.key.toLowerCase();
    let next = null;
    if (event.key === "ArrowLeft")
        next = { ...pose, x: pose.x - HUMAN_SNAP_TICKS };
    if (event.key === "ArrowRight")
        next = { ...pose, x: pose.x + HUMAN_SNAP_TICKS };
    if (event.key === "ArrowDown")
        next = { ...pose, y: pose.y - HUMAN_SNAP_TICKS };
    if (event.key === "ArrowUp")
        next = { ...pose, y: pose.y + HUMAN_SNAP_TICKS };
    if (key === "q")
        next = { ...pose, orientation: ((pose.orientation + 7) % 8) };
    if (key === "e")
        next = { ...pose, orientation: ((pose.orientation + 1) % 8) };
    if (key === "f")
        next = { ...pose, reflected: !pose.reflected };
    if (!next)
        return;
    event.preventDefault();
    mutate("manual", "set_pose", next, () => runtime.dispatch(next));
});
function toolMutation(result) {
    if (!result.ok)
        return result;
    return {
        ok: true,
        revision: result.snapshot.revision,
        validation: result.snapshot.validation,
        state: result.snapshot.state,
    };
}
const poseSchema = {
    type: "object",
    properties: {
        piece_id: { type: "string", enum: TANGRAM_CONFIG.inventory.map((piece) => piece.id) },
        x: { type: "integer", minimum: TANGRAM_CONFIG.pose_domain.x[0], maximum: TANGRAM_CONFIG.pose_domain.x[1], description: "X anchor in 1/16-unit ticks." },
        y: { type: "integer", minimum: TANGRAM_CONFIG.pose_domain.y[0], maximum: TANGRAM_CONFIG.pose_domain.y[1], description: "Y anchor in 1/16-unit ticks." },
        orientation: { type: "integer", minimum: 0, maximum: 7, description: "0..7 in 45-degree clockwise increments." },
        reflected: { type: "boolean" },
    },
    required: ["piece_id", "x", "y", "orientation", "reflected"],
    additionalProperties: false,
};
function inputPose(input) {
    return {
        id: String(input.piece_id),
        x: Number(input.x),
        y: Number(input.y),
        orientation: Number(input.orientation),
        reflected: Boolean(input.reflected),
    };
}
const tools = [
    {
        name: "tangram_get_state",
        description: "Read the seven Tangram poses, exact snap rules, validation, revision and view. No target is exposed.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        annotations: { readOnlyHint: true },
        execute() {
            const snapshot = runtime.snapshot();
            return {
                game: TANGRAM_CONFIG.game,
                configuration: {
                    config_id: TANGRAM_CONFIG.config_id,
                    game_version: TANGRAM_CONFIG.game_version,
                    state_version: TANGRAM_CONFIG.state_version,
                    equivalence_version: TANGRAM_CONFIG.equivalence_version,
                    board: TANGRAM_CONFIG.board,
                    inventory: TANGRAM_CONFIG.inventory.map(({ id, kind, area_units }) => ({ id, kind, area_units })),
                    pose_domain: TANGRAM_CONFIG.pose_domain,
                    coordinate_convention: "x/y are integer 1/16-unit anchor ticks; orientation 0..7 means 45-degree increments; reflected mirrors local X before rotation",
                },
                revision: snapshot.revision,
                state: snapshot.state,
                validation: snapshot.validation,
                presentation: { labels: borders },
            };
        },
    },
    {
        name: "tangram_set_piece_pose",
        description: "Set one Tangram piece to an absolute snapped pose and return resulting validity.",
        inputSchema: {
            ...poseSchema,
            properties: { ...poseSchema.properties, expected_revision: { type: "integer", minimum: 0 } },
        },
        execute(input) {
            const pose = inputPose(input);
            return toolMutation(mutate("tool", "set_pose", pose, () => runtime.dispatch(pose, typeof input.expected_revision === "number" ? input.expected_revision : undefined)));
        },
    },
    {
        name: "tangram_apply_actions",
        description: "Apply 1-64 absolute Tangram poses atomically; a malformed action leaves the board unchanged.",
        inputSchema: {
            type: "object",
            properties: {
                actions: { type: "array", minItems: 1, maxItems: 64, items: poseSchema },
                expected_revision: { type: "integer", minimum: 0 },
            },
            required: ["actions"],
            additionalProperties: false,
        },
        execute(input) {
            const poses = input.actions.map(inputPose);
            return toolMutation(mutate("tool", "apply_actions", poses, () => runtime.dispatchBatch(poses, typeof input.expected_revision === "number" ? input.expected_revision : undefined)));
        },
    },
    {
        name: "tangram_undo",
        description: "Undo the latest Tangram candidate-state change.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        execute: () => toolMutation(mutate("tool", "undo", undefined, () => runtime.undo())),
    },
    {
        name: "tangram_reset",
        description: "Reset all seven pieces to the initial scattered arrangement.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        execute: () => toolMutation(mutate("tool", "reset", undefined, () => runtime.reset())),
    },
    {
        name: "tangram_set_view",
        description: "Toggle human-visible piece labels/outlines without changing the candidate solution.",
        inputSchema: {
            type: "object",
            properties: { labels: { type: "boolean" } },
            required: ["labels"],
            additionalProperties: false,
        },
        execute: (input) => setBorders(Boolean(input.labels), "tool"),
    },
    {
        name: "tangram_save_state_link",
        description: "Save candidate state and presentation state into the URL fragment.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        execute: () => saveShareLink("tool"),
    },
    {
        name: "tangram_export_image",
        description: "Render the clean Tangram board as a PNG data URL.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        annotations: { readOnlyHint: true },
        execute: () => exportImage(false),
    },
    {
        name: "tangram_attest",
        description: "Validate and sign this captured Tangram revision, including the bounded client-reported history. The server does not check any target.",
        inputSchema: {
            type: "object",
            properties: { email: { type: "string" } },
            required: ["email"],
            additionalProperties: false,
        },
        execute: (input, context) => requestAttestation(String(input.email), "tool", context?.signal),
    },
];
registerWebMcpTools(tools).then((registration) => {
    webMcpStatus.textContent = registration.available
        ? "Available via " + registration.surface + " (" + tools.length + " tools)"
        : "Not available here: this browser does not expose the WebMCP modelContext API";
}, (error) => {
    webMcpStatus.textContent = "Registration failed: " + (error instanceof Error ? error.message : String(error));
});
void render(runtime.snapshot());
