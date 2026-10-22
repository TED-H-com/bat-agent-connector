import {readFileSync} from 'node:fs';
export const runtimeGolden = JSON.parse(readFileSync(new URL('../../tests/fixtures/session-runtime.json',import.meta.url),'utf8'));
// Adapt de-identified identities to the existing journey; runtime facts are unchanged.
export function runtimeCase(name:string,host='demo',sid='search-results') {
  return JSON.parse(JSON.stringify(runtimeGolden[name]).replaceAll('fixture-host',host).replaceAll('fixture-session',sid));
}
export const runtimeWords = {
  en: {waiting:'Waiting',live:'Live',attention:'Waiting for you',stale:'Observation is stale',unknown:'Output activity is unknown',streaming:'Output is arriving',idle:'No output is arriving',historical:'This observation recorded a request for you.'},
  'zh-TW': {waiting:'等待',live:'現場',attention:'等待您回覆',stale:'觀察已過期',unknown:'輸出活動未知',streaming:'正在收到輸出',idle:'目前沒有輸出',historical:'這份觀察記錄有事項等待您回覆。'},
};
