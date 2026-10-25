"""Build the OTLP/JSON trace for a run purely from stored state (dispatches, receipts, approval)."""

INTERNAL, SERVER, CLIENT = 1, 2, 3
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2


def attr(key, value):
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def build(state):
    run_attrs = [attr("ga5.run.id", state["runId"]), attr("ga5.public.marker", state["publicMarker"])]
    tid = state["traceId"]
    end_all = state.get("agentEnd") or state["serverEnd"]
    spans = []

    def span(span_id, parent, name, kind, start, end, attrs=(), status=STATUS_UNSET, links=None, extra=None):
        s = {"traceId": tid, "spanId": span_id, "name": name, "kind": kind,
             "startTimeUnixNano": str(start), "endTimeUnixNano": str(max(end or start, start)),
             "attributes": run_attrs + list(attrs), "status": {"code": status}}
        if parent:
            s["parentSpanId"] = parent
        if links:
            s["links"] = links
        if extra:
            s.update(extra)
        spans.append(s)
        return s

    server_extra = {"traceState": state["tracestate"]} if state.get("tracestate") else None
    span(state["serverSpanId"], state.get("parentSpanId"), "POST /v2/incidents", SERVER, state["serverStart"],
         max(state["serverEnd"], end_all),
         [attr("http.request.method", "POST"), attr("http.route", "/v2/incidents"), attr("http.response.status_code", 200)],
         extra=server_extra)
    agent = state["agentSpanId"]
    span(agent, state["serverSpanId"], f"invoke_agent {state['agentName']}", INTERNAL, state["serverStart"], end_all,
         [attr("gen_ai.operation.name", "invoke_agent"), attr("gen_ai.agent.name", state["agentName"])])
    span(state["chatSpanId"], agent, "chat incident-plan", CLIENT, state["chatStart"], state["chatEnd"],
         [attr("gen_ai.operation.name", "chat"), attr("gen_ai.request.model", state["model"])])

    diag_links = []
    for a in state["actions"]:
        if not a["attempts"]:
            continue  # suppressed / never executed: no execute_tool span
        last_end = max((t["end"] or t["start"]) for t in a["attempts"])
        failed = a["state"] == "failed"
        span(a["toolSpanId"], agent, f"execute_tool {a['toolName']}", INTERNAL, a["startNs"], a.get("endNs") or last_end,
             [attr("ga5.action.id", a["actionId"]), attr("gen_ai.tool.name", a["toolName"]),
              attr("gen_ai.tool.call.id", a["callId"]), attr("gen_ai.operation.name", "execute_tool")],
             status=STATUS_ERROR if failed else STATUS_UNSET)
        if a["phase"] == "diagnostic":
            diag_links.append({"traceId": tid, "spanId": a["toolSpanId"]})
        for t in a["attempts"]:
            attrs = [attr("ga5.action.id", a["actionId"]), attr("ga5.attempt", t["attempt"]),
                     attr("http.request.method", "POST"), attr("http.request.resend_count", t["attempt"] - 1)]
            if t["receiptId"]:
                attrs += [attr("ga5.receipt.id", t["receiptId"]), attr("ga5.receipt.nonce", t["nonce"])]
            status = STATUS_UNSET
            if t["status"] is not None:
                if t["status"] > 0:
                    attrs.append(attr("http.response.status_code", t["status"]))
                if t["status"] == 0 or t["status"] >= 400:
                    status = STATUS_ERROR
                    attrs.append(attr("error.type", t.get("errorType") or ("timeout" if t["status"] == 0 else str(t["status"]))))
                else:
                    status = STATUS_OK
            span(t["clientSpanId"], a["toolSpanId"], f"POST tool/{a['toolName']}", CLIENT, t["start"], t["end"] or t["start"],
                 attrs, status=status)
    js = state.get("joinSpan")
    if js:
        span(js["spanId"], agent, "incident.join", INTERNAL, js["start"], js["end"] or end_all,
             [attr("ga5.join.count", len(diag_links))], links=diag_links)
    ap = state.get("approval")
    if ap:
        attrs = [attr("ga5.approval.id", ap["approvalId"]), attr("ga5.action.id", ap["actionId"]),
                 attr("gen_ai.tool.name", ap["toolName"])]
        if ap.get("nonce"):
            attrs += [attr("ga5.approval.nonce", ap["nonce"]), attr("ga5.receipt.id", ap["receiptId"]),
                      attr("ga5.approval.decision", ap["decision"])]
        span(ap["gateSpanId"], agent, "approval_gate", INTERNAL, ap["start"], ap["end"] or end_all, attrs)
    return {"resourceSpans": [{
        "resource": {"attributes": [attr("service.name", state["agentName"]), attr("ga5.run.id", state["runId"]),
                                    attr("ga5.public.marker", state["publicMarker"])]},
        "scopeSpans": [{"scope": {"name": "ga5-incident-agent", "version": "1.0.0"}, "spans": spans}]}]}
