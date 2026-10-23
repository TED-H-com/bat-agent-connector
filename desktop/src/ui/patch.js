// Keep mounted controls/links, focus and enclosing disclosures while their
// read-only content changes. Unchanged subtrees retain their exact DOM nodes.
export function patch(current, next) {
  if (current.isEqualNode(next)) return;
  if (current.nodeType !== next.nodeType || current.nodeName !== next.nodeName) {current.replaceWith(next); return;}
  if (current.nodeType === Node.TEXT_NODE) {current.data = next.data; return;}
  for (const attr of [...current.attributes]) if (!next.hasAttribute(attr.name) && attr.name !== 'open') current.removeAttribute(attr.name);
  for (const attr of next.attributes) if (attr.name !== 'open') current.setAttribute(attr.name, attr.value);
  const old = [...current.childNodes], fresh = [...next.childNodes];
  for (let i = 0; i < Math.max(old.length, fresh.length); i++) {
    if (!old[i]) current.append(fresh[i]); else if (!fresh[i]) old[i].remove(); else patch(old[i], fresh[i]);
  }
}
