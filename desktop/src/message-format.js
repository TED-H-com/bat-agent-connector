// Deliberately limited formatting. Source text is never interpreted as HTML or a URL.
const lineText = line => line.replace(/\r?\n$/, "");
const quotePrefix = /^ {0,3}>[ \t]?/;
export function tableCells(line) {
  let text = line.trim(), cell = "", ticks = 0, separators = 0;
  const cells = [];
  if (text.startsWith("|")) text = text.slice(1);
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "\\" && text[i + 1] === "|") {cell += "|"; i++; continue;}
    if (char === "`") {
      let end = i + 1; while (text[end] === "`") end++;
      const count = end - i;
      if (!ticks) ticks = count; else if (ticks === count) ticks = 0;
      cell += text.slice(i, end); i = end - 1; continue;
    }
    if (char === "|" && !ticks) {cells.push(cell.trim()); cell = ""; separators++;}
    else cell += char;
  }
  if (cell || !text.endsWith("|")) cells.push(cell.trim());
  return separators ? cells : null;
}

export function messageBlocks(source) {
  if (source.length > 200000) return [{kind: "text", text: source}];
  const lines = source.match(/[^\n]*\n|[^\n]+$/g) || [];
  if (lines.length > 4000) return [{kind: "text", text: source}];
  const blocks = [], plain = [];
  const flush = () => {if (plain.length) blocks.push({kind: "text", text: plain.splice(0).join("")});};
  for (let i = 0; i < lines.length;) {
    const fence = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/.exec(lineText(lines[i]));
    if (fence && !(fence[1][0] === "`" && fence[2].includes("`"))) {
      flush();
      const endFence = new RegExp(`^ {0,3}${fence[1][0]}{${fence[1].length},}[ \\t]*$`);
      const body = []; i++;
      while (i < lines.length && !endFence.test(lineText(lines[i]))) body.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({kind: "code", language: fence[2].trim(), text: body.join("")});
      continue;
    }
    if (quotePrefix.test(lines[i])) {
      flush(); const body = [];
      // One explicit outer layer only. Keep nested markers, Markdown, whitespace
      // and line endings; unmarked lines are not inferred to belong to a quote.
      while (i < lines.length && quotePrefix.test(lines[i])) body.push(lines[i++].replace(quotePrefix, ""));
      blocks.push({kind: "quote", text: body.join("")}); continue;
    }
    const header = tableCells(lineText(lines[i])), divider = i + 1 < lines.length ? tableCells(lineText(lines[i + 1])) : null;
    if (header?.length && header.length <= 40 && divider?.length === header.length && divider.every(cell => /^:?-{3,}:?$/.test(cell))) {
      flush(); const rows = []; i += 2;
      while (i < lines.length && rows.length < 100) {
        if (quotePrefix.test(lines[i])) break;
        const cells = tableCells(lineText(lines[i]));
        if (!cells || cells.length !== header.length) break;
        rows.push(cells); i++;
      }
      blocks.push({kind: "table", header, rows}); continue;
    }
    plain.push(lines[i++]);
  }
  flush(); return blocks;
}

function inline(h, text) {
  // No recursive Markdown, links or HTML. Large spans remain plain text.
  if (text.length > 8192) return [text];
  const nodes = []; let from = 0;
  for (const match of text.matchAll(/`([^`\n]+)`|\*\*([^*\n]+)\*\*/g)) {
    nodes.push(text.slice(from, match.index), h(match[1] === undefined ? "strong" : "code", {}, match[1] ?? match[2]));
    from = match.index + match[0].length;
  }
  nodes.push(text.slice(from)); return nodes;
}

export function renderMessage(h, t, source, copy, {canCopyQuote = () => false} = {}) {
  return messageBlocks(source).map(block => {
    if (block.kind === "quote") return h("blockquote", {class: "message-quote"},
      h("div", {class: "message-quote-bar"},
        h("button", {class: "mini message-quote-copy", type: "button", hidden: !canCopyQuote(),
          onclick: () => {if (canCopyQuote()) copy(block.text);}}, t("message_copy_quote"))),
      h("div", {class: "message-text"}, ...inline(h, block.text)));
    if (block.kind === "code") return h("div", {class: "message-code"},
      h("div", {class: "message-code-bar"}, h("span", {class: "muted"}, block.language || t("message_code")),
        h("button", {class: "mini", type: "button", onclick: () => copy(block.text)}, t("message_copy_code"))),
      h("pre", {tabindex: "0", "aria-label": t("message_code")}, h("code", {}, block.text)));
    if (block.kind === "table") return h("div", {class: "message-table", tabindex: "0", role: "region", "aria-label": t("message_table")},
      h("table", {}, h("thead", {}, h("tr", {}, ...block.header.map(cell => h("th", {scope: "col"}, ...inline(h, cell))))),
        h("tbody", {}, ...block.rows.map(row => h("tr", {}, ...row.map(cell => h("td", {}, ...inline(h, cell))))))));
    return h("div", {class: "message-text"}, ...inline(h, block.text));
  });
}
