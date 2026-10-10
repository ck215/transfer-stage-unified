#!/usr/bin/env python3
"""Worst-case stack depth per root (main and each ISR) of an AVR ELF from `avr-objdump -d`.

Per function: the largest of (pushes so far + frame) at any instruction, where the frame comes from the
`in r28,0x3d / in r29,0x3e / sbiw|subi r28 ... / out 0x3d` pattern and `rcall .+0` (3 bytes each on a 3-byte-PC part);
plus, at each call, 3 bytes of return address and the callee's own worst depth. Indirect calls (icall/eicall) are
charged with the deepest of the functions named on the command line as their possible targets.
usage: stack_depth.py <elf.dis> <root> [--icall name,name,...]
"""
import re, sys
FUNC = re.compile(r"^([0-9a-f]+) <([^>]+)>:")
INS = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(\S+)\s*([^;<]*)(?:;\s*(0x[0-9a-f]+))?")
funcs, cur = {}, None
for raw in open(sys.argv[1]):
    m = FUNC.match(raw)
    if m: cur = m.group(2); funcs[cur] = []; continue
    m = INS.match(raw)
    if m and cur: funcs[cur].append((int(m.group(1), 16), m.group(2), m.group(3).strip(), m.group(4)))
by_addr = {v[0][0]: k for k, v in funcs.items() if v}
# --icall caller=target[+target],caller=...   the possible targets of each function's indirect calls (vtables)
icall = {}
if "--icall" in sys.argv:
    for item in sys.argv[sys.argv.index("--icall") + 1].split(","):
        c, t = item.split("="); icall[c] = [x for x in t.split("+") if x]
memo = {}
def depth(name, chain=()):
    if name in memo: return memo[name]
    if name in chain: raise SystemExit("recursion: " + " -> ".join(chain + (name,)))
    pushes, frame, worst, sp_from = 0, 0, 0, False
    pending = 0
    for addr, op, args, tgt in funcs.get(name, []):
        if op == "push": pushes += 1
        elif op == "pop": pass                     # epilogue: depth already counted
        elif op == "in" and args.replace(" ", "") in ("r28,0x3d", "r29,0x3e"): sp_from = True
        elif sp_from and op in ("sbiw", "subi") and args.startswith("r28"):
            pending = int(args.split(",")[1].strip(), 0)
        elif sp_from and op == "sbci" and args.startswith("r29"):
            pending += 256 * int(args.split(",")[1].strip(), 0)
        elif op == "out" and args.replace(" ", "").startswith("0x3d") and pending:
            frame = max(frame, pending); pending = 0; sp_from = False
        elif op == "rcall" and args.strip() == ".+0": frame += 3
        here = pushes + frame
        if op in ("call", "rcall") and args.strip() != ".+0":
            dest = int(tgt, 16) if tgt else int(args, 16)
            callee = by_addr.get(dest)
            if callee is None: continue
            if callee == name or callee.startswith("__fp_"):   # libm's internal entry points call into themselves: leaf
                here += 3 + 8                                     # routines that keep to registers; charged 8 bytes
            else:
                here += 3 + depth(callee, chain + (name,))
        elif op in ("icall", "eicall"):
            if name not in icall: raise SystemExit(f"indirect call in {name}: name its targets with --icall")
            here += 3 + max([depth(t, chain + (name,)) for t in icall[name]] or [0])
        worst = max(worst, here)
    memo[name] = worst
    return worst
root = sys.argv[2]
print(f"{root}: {depth(root)} bytes")
