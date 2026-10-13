// Longest-path layers, then a few barycentric passes. Cycles stop at the visiting node.
export function layoutGraph(nodes, links) {
  const ids = [];
  const seen = new Set();
  for (const node of nodes || []) {
    const id = node?.id;
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  const outgoing = new Map(ids.map(id => [id, []]));
  const incoming = new Map(ids.map(id => [id, []]));
  for (const link of links || []) {
    if (!outgoing.has(link?.from) || !outgoing.has(link?.to) || link.from === link.to) continue;
    if (!outgoing.get(link.from).includes(link.to)) outgoing.get(link.from).push(link.to);
    if (!incoming.get(link.to).includes(link.from)) incoming.get(link.to).push(link.from);
  }
  const layer = new Map();
  const visiting = new Set();
  const depth = id => {
    if (layer.has(id)) return layer.get(id);
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let value = 0;
    for (const pred of incoming.get(id)) value = Math.max(value, depth(pred) + 1);
    visiting.delete(id);
    layer.set(id, value);
    return value;
  };
  for (const id of ids) depth(id);
  const count = ids.length ? Math.max(...layer.values()) + 1 : 0;
  const columns = Array.from({length: count}, () => []);
  for (const id of ids) columns[layer.get(id)].push(id);
  for (const column of columns) column.sort();
  const orderOf = () => {
    const order = new Map();
    columns.forEach(column => column.forEach((id, index) => order.set(id, index)));
    return order;
  };
  const bary = (column, neighbors, order) => column.map((id, index) => {
    const refs = neighbors.get(id).map(next => order.get(next)).filter(value => value != null);
    return {id, index, score: refs.length ? refs.reduce((sum, value) => sum + value, 0) / refs.length : index};
  }).sort((a, b) => a.score - b.score || a.index - b.index).map(row => row.id);
  for (let pass = 0; pass < 4; pass++) {
    let order = orderOf();
    for (let index = 1; index < columns.length; index++) columns[index] = bary(columns[index], incoming, order);
    order = orderOf();
    for (let index = columns.length - 2; index >= 0; index--) columns[index] = bary(columns[index], outgoing, order);
  }
  // Keep the barycentric order, then drop each node beside the neighbors that point at it.
  const yOf = new Map();
  for (const column of columns) {
    const rows = column.map((id, index) => {
      const preds = incoming.get(id).map(next => yOf.get(next)).filter(value => value != null);
      const ideal = preds.length ? preds.reduce((sum, value) => sum + value, 0) / preds.length : index;
      return {id, ideal, index};
    }).sort((a, b) => a.ideal - b.ideal || a.index - b.index);
    let cursor = -Infinity;
    for (const row of rows) {
      const y = Math.max(cursor + 1, row.ideal);
      yOf.set(row.id, y);
      cursor = y;
    }
  }
  const positions = [];
  columns.forEach((column, x) => column.forEach(id => positions.push({id, x, y: yOf.get(id), layer: x})));
  return {positions, layers: count};
}
