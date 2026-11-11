const fields = ['workspace_id','link_id','work_item_id','version','created_by','source_id','entry_id'];
export function historyRecord(value) {
  const row = Object.fromEntries(fields.map(key=>[key, value?.[key] ?? value?.fixed_ref?.[key]]));
  if (fields.filter(key=>key !== 'version').some(key=>typeof row[key] !== 'string')
    || !/^ws_[0-9a-f]{32}$/.test(row.workspace_id || '') || !/^hxl_[0-9a-f]{32}$/.test(row.link_id || '')
    || !/^wi_[0-9a-f]{20}$/.test(row.work_item_id || '') || !/^shi_[0-9a-f]{32}$/.test(row.entry_id || '')
    || typeof row.source_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(row.source_id)
    || typeof row.created_by !== 'string' || !row.created_by || row.created_by.length > 256
    || !Number.isSafeInteger(row.version) || row.version < 1) throw Error('HISTORY_RECORDS_INVALID');
  return row;
}
export const historyDraftNamespace = (namespace, row) => `${namespace}.history.${JSON.stringify([row.source_id,row.entry_id])}`;
export function historyRecords(storage, namespace, workspaceId) {
  const key = `batc.pm.${namespace}.history-known.${workspaceId}`;
  function read() {
    const saved = JSON.parse(storage.getItem(key) || '{"version":1,"records":[]}');
    if (saved?.version !== 1 || !Array.isArray(saved.records) || saved.records.length > 500) throw Error('HISTORY_RECORDS_INVALID');
    const seen = new Set();
    return saved.records.map(value=>{
      const row = historyRecord(value);
      if (Object.keys(value).sort().join(',') !== [...fields].sort().join(',') || row.workspace_id !== workspaceId || seen.has(row.link_id)) throw Error('HISTORY_RECORDS_INVALID');
      seen.add(row.link_id); return row;
    });
  }
  function write(records) {
    if (records.length) storage.setItem(key, JSON.stringify({version:1,records})); else storage.removeItem(key);
  }
  return {read,
    remember(value) {
      const row = historyRecord(value);
      if (row.workspace_id !== workspaceId) throw Error('HISTORY_RECORDS_INVALID');
      const records = read(), index = records.findIndex(item=>item.link_id === row.link_id);
      if (index < 0 && records.length >= 500) return false;
      if (index < 0) records.push(row); else records[index] = row;
      write(records); return true;
    },
    remove(linkId) {write(read().filter(row=>row.link_id !== linkId));},
  };
}
