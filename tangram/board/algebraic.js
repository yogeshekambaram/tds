export const ALG_ZERO = { a: 0n, b: 0n };
export function alg(a, b = 0) {
    return { a: BigInt(a), b: BigInt(b) };
}
export function algAdd(x, y) {
    return { a: x.a + y.a, b: x.b + y.b };
}
export function algSub(x, y) {
    return { a: x.a - y.a, b: x.b - y.b };
}
export function algMul(x, y) {
    return {
        a: x.a * y.a + 2n * x.b * y.b,
        b: x.a * y.b + x.b * y.a,
    };
}
export function algNeg(x) {
    return { a: -x.a, b: -x.b };
}
export function algEq(x, y) {
    return x.a === y.a && x.b === y.b;
}
export function algSign(x) {
    if (x.a === 0n && x.b === 0n)
        return 0;
    if (x.a >= 0n && x.b >= 0n)
        return 1;
    if (x.a <= 0n && x.b <= 0n)
        return -1;
    const a2 = x.a * x.a;
    const twoB2 = 2n * x.b * x.b;
    if (x.a > 0n)
        return a2 > twoB2 ? 1 : -1;
    return twoB2 > a2 ? 1 : -1;
}
export function algCompare(x, y) {
    return algSign(algSub(x, y));
}
export function algValue(x, denominator = 16) {
    return (Number(x.a) + Number(x.b) * Math.SQRT2) / denominator;
}
export function pointEq(a, b) {
    return algEq(a.x, b.x) && algEq(a.y, b.y);
}
export function pointSub(a, b) {
    return { x: algSub(a.x, b.x), y: algSub(a.y, b.y) };
}
export function pointCompare(a, b) {
    return algCompare(a.x, b.x) || algCompare(a.y, b.y);
}
export function dot(a, b) {
    return algAdd(algMul(a.x, b.x), algMul(a.y, b.y));
}
export function cross(a, b) {
    return algSub(algMul(a.x, b.y), algMul(a.y, b.x));
}
export function pointKey(point) {
    return point.x.a + "," + point.x.b + ";" + point.y.a + "," + point.y.b;
}
