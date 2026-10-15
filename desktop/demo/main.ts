import '../src/main.ts';
import './main.css';
import {h} from '../src/ui/dom.js';
import {button} from '../src/ui/components.js';
import {locale} from './i18n.js';
import {demo, demoServer} from './transport.ts';

const zh = locale === 'zh-TW';
const view = new URLSearchParams(location.search).get('view');
const views = ['overview', 'discussions', 'requirements', 'board', 'issues', 'schedule', 'dependencies', 'agents', 'maintenance'];
if (!location.hash) location.replace('#/pm/' + demo.ids.workspaceId + (views.includes(view || '') ? '/' + view : ''));
document.documentElement.lang = zh ? 'zh-Hant' : 'en';
document.title = 'Better Agent Dashboard · ' + (zh ? '互動展示' : 'Interactive demo');
const link = (href: string, title: string) => h('a', {href, class: 'ui-button secondary ui-sm'}, title);
const languageUrl = new URL(location.href);
languageUrl.searchParams.set('lang', zh ? 'en' : 'zh-TW');
const languageLink = link(languageUrl.href, zh ? 'English' : '繁體中文');
languageLink.addEventListener('click', () => {languageUrl.hash = location.hash; languageLink.href = languageUrl.href;});
document.getElementById('demo-bar')!.append(
  h('div', {class: 'demo-description'}, h('strong', {}, zh ? 'Dashboard 展示' : 'Dashboard demo'),
    h('span', {}, zh ? '合成資料 · 可瀏覽、搜尋與篩選；不連線到 BAT 或模型。' : 'Sample data · Explore, search and filter. No BAT or model connection.')),
  h('nav', {'aria-label': zh ? '展示選單' : 'Demo navigation'},
    link('../', zh ? '專案介紹' : 'About the project'),
    link('../prototype/v1.3/?lang=' + locale, zh ? '試走 v1.3 工作流程' : 'Try the v1.3 workflow'),
    languageLink,
    button({size: 'sm', label: zh ? '重新開始' : 'Reset tour', onClick: () => {
      for (const key of Object.keys(localStorage)) if (key.startsWith('batc.') && key.includes(':' + demoServer + ':')) localStorage.removeItem(key);
      location.replace(location.pathname + '?lang=' + locale);
    }})),
);
