// Additional central-shaped execution facts used by Requirements and Board experiences.
// A task's blocked state is evidence; the board projection does not supply its reason.
import {kanbanPage, kanbanCard} from './kanban.ts';
import {unix, type DemoProject} from './project.ts';

function executionFacts(project: DemoProject, card: any) {
  if (card.work_item_id === project.ids.work.audit) {
    card.execution.tasks = [{task_id: 'demo-audit-task', state: 'blocked', paused: false, control_version: 1,
      session_id: project.ids.sessions.manual, observed_at: unix(project.now, -1), state_since: null, timing: 'unknown', completion: 'unassessed'}];
    card.execution.sessions = [{resource_id: `${project.ids.host}/${project.ids.sessions.manual}`, session_id: project.ids.sessions.manual,
      runtime_status: 'idle', observed_at: unix(project.now, -1), freshness: 'saved_observation'}];
  }
  return card;
}
export function requirementsBoard(project: DemoProject, url: URL) {
  const page = kanbanPage(project, url); page.cards = page.cards.map(card => executionFacts(project, card)); return page;
}
export function requirementsCard(project: DemoProject, id: string) {
  const result = kanbanCard(project, id); if (result) result.card = executionFacts(project, result.card); return result;
}

// Matches BrowserSessions.describe; the default demo still exercises bearer fallback.
export function requirementsBrowserSession(project: DemoProject) {
  return {csrf: 'fixture-browser-csrf-'.repeat(3), expires_at: unix(project.now, 1), actor: project.ids.actor,
    server_id: project.ids.serverId, principal_id: 'demo-reader'};
}
