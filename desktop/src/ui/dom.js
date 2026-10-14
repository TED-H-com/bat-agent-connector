// Shared safe DOM builder. Strings are always text, including provider output.
// Native replaceChildren, append, prepend, before, after and replaceWith turn
// null, undefined and false into the text "null", "undefined" or "false".
// h() already skips those. place() is the same filter for those DOM methods.
// Empty strings and numbers stay; only the three empty markers are dropped.
export function place(node, method, ...children) {
  const kept = children.flat(Infinity).filter(child => child !== null && child !== undefined && child !== false);
  node[method](...kept);
  return node;
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  if (el.matches('.chip, .reg-chip, .message-chip, .dep-chip')) {
    if (!el.hasAttribute('title')) el.title = el.textContent;
    if (!el.hasAttribute('aria-label')) el.setAttribute('aria-label', el.textContent);
  }
  return el;
}
