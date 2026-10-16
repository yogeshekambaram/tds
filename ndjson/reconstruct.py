"""Reconstruct Ollama NDJSON streams from hex-encoded byte chunks."""
import codecs
import json
import sys

STREAMS = {
    "A": ["7b22726573706f6e736522", "3a225265706f7274206a6f622d36333364383836393a", "20222c22646f6e65223a66616c73657d0a7b",
          "227468696e6b696e67223a22696e7465726e616c206e6f", "7465222c22726573706f6e7365223a22222c2264", "6f6e65223a66616c73657d0a7b22",
          "726573706f6e7365223a22636166c3", "a95c6e222c22646f6e65223a66616c7365", "7d0a7b22726573706f6e7365223a22526561647920",
          "e2", "9c93222c22646f6e65223a747275652c22", "646f6e655f726561736f6e223a2273746f70227d0a"],
    "B": ["7b22726573706f6e736522", "3a225061727469616c206a6f622d3633336438383639", "222c22646f6e65223a66616c73657d0a7b22",
          "726573706f6e7365223a22756e66696e6973686564"],
    "C": ["7b22726573706f6e736522", "3a224472616674206a6f622d3633336438383639222c", "22646f6e65223a66616c73657d0a7b226572",
          "726f72223a226d6f64656c20756e6c6f61646564227d0a", "7b22726573706f6e7365223a2249474e4f524522", "2c22646f6e65223a747275657d0a"],
}


def reconstruct(chunks):
    decoder = codecs.getincrementaldecoder("utf-8")()
    buf, text = "", []
    for chunk in chunks:
        buf += decoder.decode(bytes.fromhex(chunk))
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error") is not None:
                return {"text": "".join(text), "complete": False, "error": rec["error"]}
            text.append(rec.get("response", ""))
            if rec.get("done") is True:
                return {"text": "".join(text), "complete": True, "error": None}
    return {"text": "".join(text), "complete": False, "error": None}  # EOF: trailing partial record ignored


answer = {k: reconstruct(v) for k, v in STREAMS.items()}
with open("answer.json", "w", encoding="utf-8") as f:
    json.dump(answer, f, ensure_ascii=False)
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(answer, ensure_ascii=False, indent=1))
