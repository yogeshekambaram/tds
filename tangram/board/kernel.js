const encoder = new TextEncoder();
function comparePoint(a, b) {
    return a[0] - b[0] || a[1] - b[1];
}
function comparePointSequences(a, b) {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i += 1) {
        const cmp = comparePoint(a[i], b[i]);
        if (cmp)
            return cmp;
    }
    return a.length - b.length;
}
function equalPoint(a, b) {
    return a[0] === b[0] && a[1] === b[1];
}
function stripClosingPoint(loop) {
    const points = loop.map(([x, y]) => [x, y]);
    if (points.length > 1 && equalPoint(points[0], points.at(-1)))
        points.pop();
    return points;
}
function removeCollinear(loop) {
    let points = stripClosingPoint(loop);
    let changed = true;
    while (changed && points.length >= 3) {
        changed = false;
        const keep = points.map((point, i) => {
            const prev = points[(i + points.length - 1) % points.length];
            const next = points[(i + 1) % points.length];
            const cross = (point[0] - prev[0]) * (next[1] - point[1]) -
                (point[1] - prev[1]) * (next[0] - point[0]);
            if (cross === 0)
                changed = true;
            return cross !== 0;
        });
        points = points.filter((_, i) => keep[i]);
    }
    if (points.length < 3)
        throw new Error("boundary loop must contain at least three non-collinear points");
    return points;
}
function rotations(points) {
    return points.map((_, offset) => [
        ...points.slice(offset),
        ...points.slice(0, offset),
    ]);
}
function normalizeLoop(loop) {
    const points = removeCollinear(loop);
    const reversed = [...points].reverse();
    return [...rotations(points), ...rotations(reversed)]
        .sort(comparePointSequences)[0];
}
export function canonicalizeBoundaryLoops(loops) {
    return loops.map(normalizeLoop).sort(comparePointSequences);
}
function cellKey([x, y]) {
    return String(x) + "," + String(y);
}
function parseCellKey(key) {
    const [x, y] = key.split(",").map(Number);
    return [x, y];
}
function connectedComponents(cellKeys) {
    const remaining = new Set(cellKeys);
    const components = [];
    while (remaining.size) {
        const first = remaining.values().next().value;
        remaining.delete(first);
        const queue = [first];
        const component = [];
        while (queue.length) {
            const key = queue.pop();
            component.push(key);
            const [x, y] = parseCellKey(key);
            const neighbors = [
                String(x + 1) + "," + String(y),
                String(x - 1) + "," + String(y),
                String(x) + "," + String(y + 1),
                String(x) + "," + String(y - 1),
            ];
            for (const neighbor of neighbors) {
                if (!remaining.delete(neighbor))
                    continue;
                queue.push(neighbor);
            }
        }
        components.push(component);
    }
    return components;
}
function edgeKey(a, b) {
    return String(a[0]) + "," + String(a[1]) + ">" + String(b[0]) + "," + String(b[1]);
}
function componentBoundaryLoops(component) {
    const edges = new Map();
    for (const key of component) {
        const [x, y] = parseCellKey(key);
        const cellEdges = [
            { from: [x, y], to: [x + 1, y] },
            { from: [x + 1, y], to: [x + 1, y + 1] },
            { from: [x + 1, y + 1], to: [x, y + 1] },
            { from: [x, y + 1], to: [x, y] },
        ];
        for (const edge of cellEdges) {
            const reverse = edgeKey(edge.to, edge.from);
            if (edges.delete(reverse))
                continue;
            edges.set(edgeKey(edge.from, edge.to), edge);
        }
    }
    const outgoing = new Map();
    for (const edge of edges.values()) {
        const key = cellKey(edge.from);
        const list = outgoing.get(key) ?? [];
        list.push(edge);
        outgoing.set(key, list);
    }
    const unused = new Set(edges.keys());
    const loops = [];
    while (unused.size) {
        const firstKey = [...unused].sort()[0];
        const first = edges.get(firstKey);
        const start = first.from;
        const loop = [start];
        let edge = first;
        while (true) {
            unused.delete(edgeKey(edge.from, edge.to));
            if (equalPoint(edge.to, start))
                break;
            loop.push(edge.to);
            const candidates = (outgoing.get(cellKey(edge.to)) ?? [])
                .filter((candidate) => unused.has(edgeKey(candidate.from, candidate.to)))
                .sort((a, b) => comparePoint(a.to, b.to));
            if (!candidates.length)
                throw new Error("open boundary in cell union");
            edge = candidates[0];
        }
        loops.push(loop);
    }
    return loops;
}
export function canonicalizeCellUnion(cells) {
    const keys = new Set(cells.map(cellKey));
    const loops = connectedComponents(keys).flatMap(componentBoundaryLoops);
    return { loops: canonicalizeBoundaryLoops(loops) };
}
export function buildMatchKeyInput(config, canonicalBytes) {
    const header = encoder.encode("tds-visual-match\0\n" +
        "game=" + config.game + "\n" +
        "game_version=" + String(config.game_version) + "\n" +
        "config=" + config.config_id + "\n" +
        "equivalence=" + String(config.equivalence_version) + "\n" +
        "canonical_bytes=" + String(canonicalBytes.byteLength) + "\n");
    const result = new Uint8Array(header.byteLength + canonicalBytes.byteLength);
    result.set(header);
    result.set(canonicalBytes, header.byteLength);
    return result;
}
function bytesToHex(bytes) {
    return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
export async function computeMatchKey(adapter, state, config) {
    const validation = adapter.validate(state, config);
    if (validation.invalid)
        throw new Error("cannot compute match key for invalid candidate");
    const canonical = adapter.canonicalize(state, config);
    const canonicalBytes = adapter.encodeCanonical(canonical, config);
    const input = buildMatchKeyInput(config, canonicalBytes);
    const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(input).buffer);
    return bytesToHex(new Uint8Array(digest));
}
