"""Fresh, non-actuating benchmark of the manual studio's real parsing pipeline.

The corpus is a small authored development set, not a representative language
distribution. Exact action/valve/amount matching is required, including order.
"""
import json
from collections import Counter
from pathlib import Path

from sim_app.ai import PARSER_MODEL, parse_commands


def action(kind, valve=None, amount=None):
    return [kind, valve, amount]


CASES = [
    ("absolute", "Set feed 1 to 37 percent", [action("set", "f1", 37)]),
    ("absolute", "Set feed 2 to 18 percent", [action("set", "f2", 18)]),
    ("absolute", "Put the purge at 12 percent", [action("set", "purge", 12)]),
    ("absolute", "Set the product valve to 56%", [action("set", "product", 56)]),
    ("absolute", "Lower feed 1 to 22%", [action("set", "f1", 22)]),
    ("absolute", "Bring feed 2 to 9%", [action("set", "f2", 9)]),
    ("relative", "Increase feed 1 by 7 percent", [action("adjust", "f1", 7)]),
    ("relative", "Reduce feed 2 by 6 percent", [action("adjust", "f2", -6)]),
    ("relative", "Open purge by 8%", [action("adjust", "purge", 8)]),
    ("relative", "Close product by 4%", [action("adjust", "product", -4)]),
    ("relative", "Raise product by 2.5 percent", [action("adjust", "product", 2.5)]),
    ("relative", "Cut feed 1 by 11%", [action("adjust", "f1", -11)]),
    ("endpoints", "Shut the purge valve", [action("set", "purge", 0)]),
    ("endpoints", "Fully open the product valve", [action("set", "product", 100)]),
    ("endpoints", "Close feed 1 completely", [action("set", "f1", 0)]),
    ("endpoints", "Open feed 2 fully", [action("set", "f2", 100)]),
    ("mode", "Rescue the reactor", [action("rescue")]),
    ("mode", "Please stabilise the reactor", [action("rescue")]),
    ("mode", "Stop AI control", [action("stop")]),
    ("mode", "Hand control back to me", [action("stop")]),
    ("unsupported", "Open the coolant valve to 30%", [action("unknown")]),
    ("unsupported", "What is the weather tomorrow?", [action("unknown")]),
    ("multiple", "Set feed 1 to 33% then set feed 2 to 16%", [action("set", "f1", 33), action("set", "f2", 16)]),
    ("multiple", "Increase purge by 9% then reduce product by 2%", [action("adjust", "purge", 9), action("adjust", "product", -2)]),
    ("multiple", "Shut feed 2 then open purge to 70%", [action("set", "f2", 0), action("set", "purge", 70)]),
    ("multiple", "Stop the AI then set product to 45%", [action("stop"), action("set", "product", 45)]),
    ("multiple", "Set purge to 14% then rescue the reactor", [action("set", "purge", 14), action("rescue")]),
    ("multiple", "Close product by 7% then increase feed 1 by 3%", [action("adjust", "product", -7), action("adjust", "f1", 3)]),
    ("context", "Open the purge by 13% to lower the pressure", [action("adjust", "purge", 13)]),
    ("context", "Close feed 2 by 8% because pressure is high", [action("adjust", "f2", -8)]),
]


def normalized(items):
    return [action(x.get("action"), x.get("valve") if x.get("action") in ("set", "adjust") else None,
                   x.get("amount") if x.get("action") in ("set", "adjust") else None) for x in items]


def main():
    report = {"model": PARSER_MODEL, "n": len(CASES), "method": "authored development corpus; actual Ollama inference; no simulator access",
              "rows": []}
    path = Path("artifacts/command-parser-accuracy.json")
    path.parent.mkdir(exist_ok=True)
    for index, (category, text, gold) in enumerate(CASES):
        try:
            parsed = parse_commands(text)
            prediction = normalized(parsed["actions"])
            raw = json.loads(parsed["model_output"])
            raw_prediction = normalized(raw.get("actions", []))
            row = {"category": category, "text": text, "gold": gold, "prediction": prediction,
                   "correct": prediction == gold, "raw_correct": raw_prediction == gold,
                   "model_output": parsed["model_output"], "latency_s": parsed["latency_s"],
                   "corrections": [fix for item in parsed["actions"] for fix in item["corrections"]]}
        except Exception as exc:
            row = {"category": category, "text": text, "gold": gold, "correct": False,
                   "raw_correct": False, "error": str(exc)}
        report["rows"].append(row)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if (index + 1) % 5 == 0:
            print(f"{index + 1}/{len(CASES)} commands evaluated", flush=True)
    counts = Counter(row["category"] for row in report["rows"])
    correct = Counter(row["category"] for row in report["rows"] if row["correct"])
    report.update(exact_command_accuracy=sum(row["correct"] for row in report["rows"]) / len(CASES),
                  raw_model_exact_accuracy=sum(row["raw_correct"] for row in report["rows"]) / len(CASES),
                  by_category={k: {"correct": correct[k], "n": count, "accuracy": correct[k]/count} for k, count in counts.items()})
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
