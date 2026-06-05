// Composite strategy builder — three-section editor (Indicators / Entry / Exit).
// State lives in a closure; the whole editor is re-rendered on every mutation
// (the tree is tiny, ~50 nodes). On submit, backtest.js calls
// readCompositeParams() to extract the JSON shape the backend expects.

(function () {
  // ── Indicator type metadata. Mirrors strategies/indicators.py:INDICATOR_REGISTRY.
  // `args` describe the inputs each type takes; defaults pre-fill new rows.
  const INDICATOR_TYPES = {
    sma:       { label: "SMA",       args: [{ key: "period", default: 20,  type: "int"   }] },
    ema:       { label: "EMA",       args: [{ key: "period", default: 20,  type: "int"   }] },
    rsi:       { label: "RSI",       args: [{ key: "period", default: 14,  type: "int"   }] },
    macd:      { label: "MACD",      args: [
                                      { key: "fast",   default: 12, type: "int" },
                                      { key: "slow",   default: 26, type: "int" },
                                      { key: "signal", default: 9,  type: "int" }] },
    bollinger: { label: "Bollinger", args: [
                                      { key: "period", default: 20,  type: "int"   },
                                      { key: "stddev", default: 2.0, type: "float" }] },
    atr:       { label: "ATR",       args: [{ key: "period", default: 14,  type: "int"   }] },
  };

  const OPERATORS = [
    [">",  ">"],
    ["<",  "<"],
    [">=", "≥"],
    ["<=", "≤"],
    ["==", "="],
    ["crosses_above", "↗ crosses above"],
    ["crosses_below", "↘ crosses below"],
  ];

  const PRICE_FIELDS = ["close", "high", "low", "open"];

  // ── State (closure, single instance). Default: SMA(50)/SMA(200) crossover —
  // a working example the user can tweak.
  let state = defaultState();

  function defaultState() {
    return {
      indicators: [
        { id: "sma_50",  type: "sma", args: { period: 50  } },
        { id: "sma_200", type: "sma", args: { period: 200 } },
      ],
      entry: [
        { lhs: { kind: "indicator", id: "sma_50" },
          op:  ">",
          rhs: { kind: "indicator", id: "sma_200" } },
      ],
      exit: [
        { lhs: { kind: "indicator", id: "sma_50" },
          op:  "<",
          rhs: { kind: "indicator", id: "sma_200" } },
      ],
    };
  }

  // ── Auto-alias derivation, mirrors strategies.indicators.auto_alias.
  function autoAlias(type, args) {
    const meta = INDICATOR_TYPES[type];
    const parts = [type, ...meta.args.map(a => String(args[a.key]).replace(".", "p"))];
    return parts.join("_");
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  let mountEl = null;

  function renderCompositeBuilder(container) {
    mountEl = container;
    rerender();
  }

  function readCompositeParams() {
    return {
      indicators: state.indicators.map(({ id, type, args }) => ({
        id, type, args: { ...args },
      })),
      entry: { all: state.entry.map(c => ({ ...c })) },
      exit:  { any: state.exit .map(c => ({ ...c })) },
    };
  }

  window.renderCompositeBuilder = renderCompositeBuilder;
  window.readCompositeParams    = readCompositeParams;

  // ── Rendering ──────────────────────────────────────────────────────────────

  function rerender() {
    if (!mountEl) return;
    mountEl.innerHTML = "";
    mountEl.appendChild(renderSection(
      "Indicators",
      "Pick the indicators you want to reference in the rules below.",
      state.indicators.map((ind, i) => renderIndicatorRow(ind, i)),
      () => addIndicator(),
    ));
    mountEl.appendChild(renderSection(
      "Entry — BUY when ALL conditions are true",
      "Signal +1 fires when every condition holds.",
      state.entry.map((c, i) => renderConditionRow(c, i, "entry")),
      () => addCondition("entry"),
    ));
    mountEl.appendChild(renderSection(
      "Exit — SELL when ANY condition is true",
      "Signal −1 fires when any one of these holds.",
      state.exit.map((c, i) => renderConditionRow(c, i, "exit")),
      () => addCondition("exit"),
    ));
  }

  function renderSection(title, subtitle, rows, onAdd) {
    const sec = el("div", "builder-section");
    const head = el("div", "builder-section__head");
    head.appendChild(el("span", "builder-section__title", title));
    head.appendChild(el("span", "builder-section__sub", subtitle));
    const addBtn = el("button", "builder-btn builder-btn--add", "+ ADD");
    addBtn.type = "button";
    addBtn.addEventListener("click", onAdd);
    head.appendChild(addBtn);
    sec.appendChild(head);

    if (!rows.length) {
      sec.appendChild(el("div", "builder-empty", "(none)"));
    } else {
      const list = el("div", "builder-rows");
      rows.forEach(r => list.appendChild(r));
      sec.appendChild(list);
    }
    return sec;
  }

  function renderIndicatorRow(ind, idx) {
    const row = el("div", "builder-row");

    const typeSelect = makeSelect(
      Object.entries(INDICATOR_TYPES).map(([k, v]) => [k, v.label]),
      ind.type,
      (newType) => {
        const meta = INDICATOR_TYPES[newType];
        const newArgs = {};
        meta.args.forEach(a => { newArgs[a.key] = a.default; });
        state.indicators[idx] = {
          type: newType,
          args: newArgs,
          id: autoAlias(newType, newArgs),
        };
        cascadeIndicatorRename(ind.id, state.indicators[idx].id);
        rerender();
      },
    );
    row.appendChild(typeSelect);

    INDICATOR_TYPES[ind.type].args.forEach(arg => {
      const wrap = el("label", "builder-arg");
      wrap.appendChild(el("span", "builder-arg__label", arg.key));
      const input = document.createElement("input");
      input.type = "number";
      input.step = arg.type === "float" ? "0.1" : "1";
      input.value = ind.args[arg.key];
      input.className = "builder-arg__input";
      input.addEventListener("change", () => {
        const v = arg.type === "float" ? parseFloat(input.value) : parseInt(input.value, 10);
        if (!Number.isFinite(v)) return;
        ind.args[arg.key] = v;
        const oldId = ind.id;
        ind.id = autoAlias(ind.type, ind.args);
        cascadeIndicatorRename(oldId, ind.id);
        rerender();
      });
      wrap.appendChild(input);
      row.appendChild(wrap);
    });

    const idChip = el("span", "builder-chip", `→ ${ind.id}`);
    row.appendChild(idChip);

    row.appendChild(removeButton(() => {
      state.indicators.splice(idx, 1);
      // Drop conditions that referenced the removed indicator
      state.entry = state.entry.filter(c => !operandReferences(c.lhs, ind.id) && !operandReferences(c.rhs, ind.id));
      state.exit  = state.exit .filter(c => !operandReferences(c.lhs, ind.id) && !operandReferences(c.rhs, ind.id));
      rerender();
    }));

    return row;
  }

  function renderConditionRow(cond, idx, section) {
    const row = el("div", "builder-row builder-row--condition");
    row.appendChild(renderOperand(cond.lhs, (newOp) => { cond.lhs = newOp; rerender(); }));
    row.appendChild(makeSelect(
      OPERATORS,
      cond.op,
      (newOp) => { cond.op = newOp; rerender(); },
    ));
    row.appendChild(renderOperand(cond.rhs, (newOp) => { cond.rhs = newOp; rerender(); }));
    row.appendChild(removeButton(() => {
      state[section].splice(idx, 1);
      rerender();
    }));
    return row;
  }

  function renderOperand(operand, onChange) {
    const wrap = el("span", "builder-operand");

    // The kind selector — combined into a single dropdown that lists all
    // currently-available choices grouped (price | indicator | const).
    const select = document.createElement("select");
    select.className = "builder-arg__input";

    const groups = [];
    groups.push({
      label: "PRICE",
      options: PRICE_FIELDS.map(f => [`price:${f}`, f.toUpperCase()]),
    });
    if (state.indicators.length) {
      groups.push({
        label: "INDICATORS",
        options: state.indicators.map(i => [`ind:${i.id}`, i.id]),
      });
    }
    groups.push({
      label: "CONST",
      options: [["const", "constant…"]],
    });

    for (const g of groups) {
      const og = document.createElement("optgroup");
      og.label = g.label;
      for (const [val, lbl] of g.options) {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = lbl;
        og.appendChild(opt);
      }
      select.appendChild(og);
    }
    select.value = operandToSelectValue(operand);

    select.addEventListener("change", () => {
      onChange(selectValueToOperand(select.value, operand));
    });
    wrap.appendChild(select);

    if (operand.kind === "const") {
      const input = document.createElement("input");
      input.type = "number";
      input.step = "0.01";
      input.value = operand.value;
      input.className = "builder-arg__input builder-arg__input--const";
      input.addEventListener("change", () => {
        const v = parseFloat(input.value);
        if (Number.isFinite(v)) onChange({ kind: "const", value: v });
      });
      wrap.appendChild(input);
    }
    return wrap;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function operandToSelectValue(op) {
    if (op.kind === "indicator") return `ind:${op.id}`;
    if (op.kind === "price")     return `price:${op.field}`;
    return "const";
  }

  function selectValueToOperand(value, prev) {
    if (value.startsWith("ind:"))   return { kind: "indicator", id: value.slice(4) };
    if (value.startsWith("price:")) return { kind: "price", field: value.slice(6) };
    return { kind: "const", value: prev.kind === "const" ? prev.value : 0 };
  }

  function operandReferences(op, id) {
    return op.kind === "indicator" && op.id === id;
  }

  function cascadeIndicatorRename(oldId, newId) {
    if (oldId === newId) return;
    // If another indicator already uses newId, append _2, _3, ...
    let unique = newId;
    let n = 2;
    while (state.indicators.filter(i => i.id === unique).length > 1) {
      unique = `${newId}_${n++}`;
    }
    if (unique !== newId) {
      const target = state.indicators.find(i => i.id === newId && i.id !== oldId);
      // Re-find since equal newIds may exist; we want the NEW one we just set.
      // Simpler: rename the one we just changed.
      const newest = state.indicators[state.indicators.length - 1];
      newest.id = unique;
    }
    // Update conditions referencing oldId
    [...state.entry, ...state.exit].forEach(c => {
      if (c.lhs.kind === "indicator" && c.lhs.id === oldId) c.lhs.id = unique;
      if (c.rhs.kind === "indicator" && c.rhs.id === oldId) c.rhs.id = unique;
    });
  }

  function addIndicator() {
    const meta = INDICATOR_TYPES.sma;
    const args = {};
    meta.args.forEach(a => { args[a.key] = a.default; });
    let id = autoAlias("sma", args);
    let n = 2;
    while (state.indicators.some(i => i.id === id)) id = `${autoAlias("sma", args)}_${n++}`;
    state.indicators.push({ id, type: "sma", args });
    rerender();
  }

  function addCondition(section) {
    const ref = state.indicators[0]?.id;
    const lhs = ref ? { kind: "indicator", id: ref } : { kind: "price", field: "close" };
    const rhs = state.indicators[1]
      ? { kind: "indicator", id: state.indicators[1].id }
      : { kind: "const", value: 0 };
    state[section].push({ lhs, op: ">", rhs });
    rerender();
  }

  function removeButton(onClick) {
    const btn = el("button", "builder-btn builder-btn--del", "×");
    btn.type = "button";
    btn.addEventListener("click", onClick);
    return btn;
  }

  function makeSelect(options, value, onChange) {
    const sel = document.createElement("select");
    sel.className = "builder-arg__input";
    for (const [v, label] of options) {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = label;
      if (v === value) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => onChange(sel.value));
    return sel;
  }

  function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text != null) e.textContent = text;
    return e;
  }
})();
