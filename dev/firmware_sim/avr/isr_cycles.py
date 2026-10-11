#!/usr/bin/env python3
"""Worst- and best-case cycle counts through an AVR function (an ISR) from `avr-objdump -d` of the whole ELF.

Builds the instruction-level control-flow graph (conditional branches, skips, rjmp/jmp), refuses any cycle (a loop
would need a bound), follows `call`s into their callees (their own longest/shortest paths, ret included), and takes
the longest and shortest path from the entry to reti/ret. Cycle counts are the ATmega2560's (AVRe+ core, 3-byte PC:
call 5, ret/reti 5). The interrupt response (5 cycles, plus up to 4 to finish the interrupted instruction) and the
vector table's jmp (3) are added for the ISR.

usage: isr_cycles.py <elf.dis> <function> [--no-call] [--trace best|worst]
  --no-call   paths that make no call at all (the ISR's fast path: no sensor change, no switch pressed while moving)
"""
import re
import sys

ONE = set("add adc sub sbc subi sbci and andi or ori eor com neg inc dec tst clr ser mov movw ldi cp cpc cpi lsl lsr rol "
          "ror asr swap bst bld sec clc sen cln sez clz sei cli ses cls sev clv set clt seh clh in out nop wdr sleep "
          "sbr cbr bset bclr".split())
TWO = set("adiw sbiw mul muls mulsu fmul fmuls fmulsu ld ldd st std lds sts push pop rjmp ijmp eijmp sbi cbi".split())
SKIP = set("sbrc sbrs cpse sbic sbis".split())
LINE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(\S+)\s*([^;<]*)(?:;\s*(0x[0-9a-f]+))?", re.I)
FUNC = re.compile(r"^([0-9a-f]+) <([^>]+)>:")
NEG = -(1 << 30)


def load(path):
    funcs, cur = {}, None
    for raw in open(path):
        m = FUNC.match(raw)
        if m:
            cur = m.group(2)
            funcs[cur] = []
            continue
        m = LINE.match(raw)
        if m and cur:
            addr = int(m.group(1), 16)
            funcs[cur].append((addr, len(m.group(2).split()), m.group(3).lower(), m.group(4).strip(), m.group(5)))
    return funcs


class Analysis:
    def __init__(self, funcs, no_call):
        self.funcs, self.no_call = funcs, no_call
        self.by_addr = {ins[0][0]: name for name, ins in funcs.items() if ins}
        self.cache, self.graphs = {}, {}

    def edges(self, ins, k):
        addr, size, op, args, tgt = ins[k]
        nxt = addr + size
        if op in ("reti", "ret"):
            return [(None, 5)]
        if op in ("rjmp", "jmp"):
            dest = int(tgt, 16) if tgt else int(args, 16)
            return [(dest, 2 if op == "rjmp" else 3)]
        if op.startswith("br"):
            return [(nxt, 1), (int(tgt, 16), 2)]
        if op in SKIP:
            skipped = ins[k + 1][1] if k + 1 < len(ins) else 2
            return [(nxt, 1), (nxt + skipped, 1 + skipped // 2)]
        if op in ("call", "rcall"):
            dest = int(tgt, 16) if tgt else int(args, 16)
            if self.no_call:
                return [(nxt, None)]
            name = self.by_addr.get(dest)
            if name is None:
                raise SystemExit(f"call to unknown {dest:#x}")
            hi, lo = self.function(name)
            c = 5 if op == "call" else 4
            return [(nxt, (hi + c, lo + c))]
        if op in ONE:
            return [(nxt, 1)]
        if op in TWO:
            return [(nxt, 2)]
        if op in ("lpm", "elpm"):
            return [(nxt, 3)]
        raise SystemExit(f"unknown op {op} at {addr:#x}")

    def function(self, name):
        if name in self.cache:
            return self.cache[name]
        ins = self.funcs[name]
        index = {a: k for k, (a, *_rest) in enumerate(ins)}
        memo, state = {}, {}

        def walk(k):
            if k in memo:
                return memo[k]
            if state.get(k) == 1:
                raise SystemExit(f"{name}: cycle through {ins[k][0]:#x}: a loop needs a bound")
            state[k] = 1
            hi, lo = NEG, None
            for to, c in self.edges(ins, k):
                if c is None:                       # a call excluded by --no-call
                    continue
                ch, cl = c if isinstance(c, tuple) else (c, c)
                if to is None:
                    h, l = ch, cl
                else:
                    if to not in index:
                        raise SystemExit(f"{name}: edge out of the function {ins[k][0]:#x} -> {to:#x}")
                    h2, l2 = walk(index[to])
                    if h2 == NEG:
                        continue
                    h, l = h2 + ch, l2 + cl
                hi = max(hi, h)
                lo = l if lo is None else min(lo, l)
            state[k] = 2
            memo[k] = (hi, lo if lo is not None else NEG)
            return memo[k]

        sys.setrecursionlimit(100000)
        self.cache[name] = walk(0)
        self.graphs[name] = (ins, index, memo)
        return self.cache[name]

    def trace(self, name, want):
        self.function(name)
        ins, index, memo = self.graphs[name]
        k, total = 0, 0
        while True:
            options = []
            for to, c in self.edges(ins, k):
                if c is None:
                    continue
                ch, cl = c if isinstance(c, tuple) else (c, c)
                cc = ch if want == "worst" else cl
                if to is None:
                    options.append((cc, to, cc))
                elif memo[index[to]][0] != NEG:
                    h, l = memo[index[to]]
                    options.append(((h if want == "worst" else l) + cc, to, cc))
            v, to, c = (max if want == "worst" else min)(options, key=lambda o: o[0])
            total += c
            a, _, op, args, _ = ins[k]
            print(f"{a:#06x} {c:3d} {total:5d}  {op} {args}")
            if to is None:
                return
            k = index[to]


def main():
    path, name = sys.argv[1], sys.argv[2]
    no_call = "--no-call" in sys.argv
    an = Analysis(load(path), no_call)
    if "--trace" in sys.argv:
        an.trace(name, sys.argv[sys.argv.index("--trace") + 1])
    worst, best = an.function(name)
    entry = 5 + 3
    print(f"{name}{' (no calls)' if no_call else ''}: body best={best} worst={worst} cycles; "
          f"with the interrupt entry ({entry}) and up to 4 cycles to finish the interrupted instruction: "
          f"best {best + entry} = {(best + entry) / 16:.2f} us, worst {worst + entry + 4} = {(worst + entry + 4) / 16:.2f} us")


if __name__ == "__main__":
    main()
