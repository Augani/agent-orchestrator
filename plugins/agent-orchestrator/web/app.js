'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = String(text);
    if (className) el.className = className;
    return el;
  };
  const state = { projects: [], project: null, agent: null, tab: 'messages', paused: false,
    filter: 'all', generation: 0, refreshing: false, detail: null, feedback: [], drafts: new Map() };
  let token = new URLSearchParams(location.hash.slice(1)).get('token');
  // The launch capability stays in this tab, never in URLs sent to the server or localStorage.
  try {
    if (token) sessionStorage.setItem('orchestrator-token', token);
    else token = sessionStorage.getItem('orchestrator-token');
  } catch (_) { /* In-memory access still works when browser storage is disabled. */ }
  if (location.hash.startsWith('#token=')) history.replaceState(null, '', location.pathname);

  async function api(path, answer) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
    const response = await fetch(path, { signal: controller.signal, method: answer === undefined ? 'GET' : 'POST',
      cache: 'no-store', credentials: 'omit', headers: { 'X-Orchestrator-Token': token || '',
        ...(answer === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(answer === undefined ? {} : { body: JSON.stringify({ answer }) }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The local request failed.');
    return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The local server took too long to respond. Refresh before retrying an answer.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  const label = value => String(value || 'Not reported').replaceAll('_', ' ');
  const count = value => value === null || value === undefined ? 'Unavailable' : Number(value).toLocaleString();
  const when = value => value ? new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—';
  function ago(value) {
    if (!value) return 'No activity yet';
    const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
    return seconds < 60 ? 'Just now' : seconds < 3600 ? `${Math.floor(seconds / 60)} minutes ago` : seconds < 86400 ? `${Math.floor(seconds / 3600)} hours ago` : new Date(value).toLocaleDateString();
  }
  function elapsed(value) {
    if (value === null || value === undefined) return '—';
    const minutes = Math.floor(value / 60);
    return minutes < 1 ? '<1m' : minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  }
  function tone(item) { return item.attention_required ? 'amber' : item.active ? 'blue' : item.state === 'accepted' || item.completed ? 'green' : 'neutral'; }
  function dot(color) { const el = node('span', null, `dot ${color}`); el.setAttribute('aria-hidden', 'true'); return el; }
  function statusLabel(job) { return ({ needs_input: 'Needs answer', awaiting_review: 'Awaiting review', accepted: 'Accepted', running: 'Running', lost: 'Disconnected' })[job.state] || label(job.state); }
  function agentName(job) { return job.cli_agent || job.model || job.cli || job.job_id; }
  function metric(title, value, detail) {
    const el = node('div', null, 'metric');
    el.append(node('div', title, 'label'), node('strong', value));
    if (detail) el.append(node('small', detail));
    return el;
  }
  // Reconcile keyed controls in place so polling preserves keyboard focus and answer text.
  function reconcile(parent, items, key, create, update) {
    const existing = new Map([...parent.children].map(el => [el.dataset.key, el]));
    let cursor = parent.firstElementChild;
    for (const item of items) {
      const id = key(item);
      const el = existing.get(id) || create(item);
      el.dataset.key = id;
      update(el, item);
      if (el !== cursor) parent.insertBefore(el, cursor);
      cursor = el.nextElementSibling;
      existing.delete(id);
    }
    for (const el of existing.values()) el.remove();
  }
  const selectedProject = () => state.projects.find(project => project.id === state.project);
  function renderAgentNavigation(project) {
    const items = project ? [{ key: 'orchestrator', orchestrator: true }, ...project.jobs.map(job => ({ ...job, key: job.job_id }))] : [];
    $('agent-count').textContent = project ? project.jobs.length : 0;
    $('agent-navigation-empty').hidden = items.length > 0;
    $('agent-navigation-empty').textContent = project ? 'No agents are available for this project.' : 'Select a project to see its agents.';
    reconcile($('agent-navigation'), items, item => item.key, () => {
      const button = node('button', null, 'agent-nav-button'); button.type = 'button';
      const text = node('span', null, 'agent-nav-copy'); text.append(node('strong'), node('small'));
      button.append(dot('neutral'), text, node('span', null, 'agent-nav-status'));
      button.addEventListener('click', () => button.dataset.agentId ? selectAgent(button.dataset.agentId) : selectOrchestrator());
      return button;
    }, (button, item) => {
      const selected = item.orchestrator ? state.agent === null : state.agent === item.job_id;
      const questions = item.orchestrator ? project.pending_feedback.length : item.pending_questions.length;
      button.dataset.agentId = item.orchestrator ? '' : item.job_id;
      button.setAttribute('aria-current', String(selected));
      button.querySelector('.dot').className = `dot ${item.orchestrator ? questions ? 'amber' : 'neutral' : tone(item)}`;
      button.querySelector('strong').textContent = item.orchestrator ? 'Project overview' : agentName(item);
      button.querySelector('small').textContent = item.orchestrator
        ? questions ? `${questions} project question${questions === 1 ? '' : 's'} waiting` : 'All agents, plans, and project feedback'
        : item.current_work || item.role || 'No progress update yet';
      button.querySelector('.agent-nav-status').textContent = item.orchestrator ? questions ? 'Needs answer' : 'Ready' : statusLabel(item);
      button.setAttribute('aria-label', `${button.querySelector('strong').textContent}: ${button.querySelector('.agent-nav-status').textContent}. ${button.querySelector('small').textContent}`);
    });
  }
  function renderOverview() {
    const p = selectedProject();
    const selected = p?.jobs.find(job => job.job_id === state.agent) || null;
    $('project-count').textContent = state.projects.length;
    reconcile($('projects'), state.projects, item => item.id, item => {
      const button = node('button', null, 'project-button'); button.type = 'button';
      const title = node('div'); title.append(node('strong'), node('small'));
      button.append(dot('neutral'), title, node('span', null, 'count'));
      button.addEventListener('click', () => selectProject(item.id));
      return button;
    }, (button, item) => {
      button.setAttribute('aria-current', String(item.id === state.project));
      button.children[0].className = `dot ${tone(item)}`;
      button.querySelector('strong').textContent = item.name;
      button.querySelector('small').textContent = item.question_count ? `${item.question_count} question${item.question_count === 1 ? '' : 's'} waiting` : item.active ? `${item.active} active · ${item.jobs.length} agents` : `${item.jobs.length} agents · history`;
      button.querySelector('.count').textContent = item.jobs.length;
      button.title = `${item.name} · ${item.id}`;
    });
    renderAgentNavigation(p);
    $('project-title').textContent = selected ? agentName(selected) : p ? p.name : 'Your projects';
    $('project-subtitle').textContent = selected
      ? `${selected.current_work || selected.role || 'Agent work'} · ${p.name}`
      : p ? `${p.jobs.length} agents · ${p.plans.length} plans · Updated ${ago(p.updated_at).toLowerCase()}` : 'Jobs and project feedback appear here when created.';
    $('filter').disabled = Boolean(selected);
    const health = $('health'); health.replaceChildren();
    if (selected) {
      const statusMetric = metric('Agent status', statusLabel(selected), selected.role || 'Worker');
      statusMetric.querySelector('strong').prepend(dot(tone(selected)));
      health.append(statusMetric, metric('Current phase', label(selected.phase), selected.recent_message ? 'Progress reported' : 'No progress update'), metric('Elapsed', elapsed(selected.elapsed_seconds), selected.active ? 'Still running' : 'Latest run'), metric('Files changed', count(selected.changed_files), 'From recorded baseline'));
    } else if (p) {
      const healthMetric = metric('Overall health', p.attention_required ? 'Question waiting' : p.active ? 'In progress' : p.completed ? 'History available' : 'No active jobs', `${p.active} active · ${p.question_count} questions`);
      healthMetric.querySelector('strong').prepend(dot(tone(p)));
      const total = p.jobs.length;
      const progressMetric = metric('Review progress', total ? `${Math.round(p.completed / total * 100)}%` : '—', `${p.completed} of ${total} agents accepted`);
      const progress = node('progress'); progress.max = total || 1; progress.value = p.completed; progress.setAttribute('aria-label', 'Agents accepted after review');
      progressMetric.querySelector('strong').after(progress);
      health.append(healthMetric, progressMetric, metric('Latest activity', ago(p.updated_at), 'Local durable state'), metric('Review queue', count(p.review_queue), `${p.failed} failed or scope-violating runs in history`));
    }
    const rows = selected ? [selected] : p ? p.jobs.filter(j => state.filter === 'all' || state.filter === 'attention' && j.attention_required || state.filter === 'active' && j.active || state.filter === 'complete' && j.state === 'accepted') : [];
    $('agents-heading').textContent = selected ? 'Selected agent work' : `Agents (${rows.length})`;
    $('table-empty').hidden = rows.length > 0;
    $('table-empty').textContent = !p ? 'No projects yet. Launch a job or request project feedback to get started.' : p.jobs.length ? 'No agents match this filter.' : 'No worker jobs in this project. Select Project overview to review its feedback.';
    reconcile($('agents'), rows, j => j.job_id, j => {
      const row = node('tr');
      for (let i = 0; i < 6; i++) row.append(node('td'));
      const button = node('button', null, 'agent-button'); button.type = 'button';
      button.append(dot('neutral'), node('span'));
      button.addEventListener('click', () => selectAgent(j.job_id));
      row.children[0].append(button, node('small'));
      for (let i = 1; i < 6; i++) row.children[i].append(node('span'), node('small'));
      row.children[5].className = 'state-cell';
      return row;
    }, (row, j) => {
      row.classList.toggle('selected', j.job_id === state.agent);
      const cells = row.children;
      const button = cells[0].querySelector('button');
      button.setAttribute('aria-pressed', String(j.job_id === state.agent));
      button.querySelector('.dot').className = `dot ${tone(j)}`;
      button.querySelector('span:last-child').textContent = agentName(j);
      cells[0].querySelector('small').textContent = j.role || 'Worker';
      const values = [[j.cli || 'Configured CLI', j.model || 'Configured model'], [j.current_work || 'No progress message yet', `${count(j.changed_files)} files · Usage ${j.usage ? 'reported' : 'unavailable'}`], [label(j.phase), j.recent_message ? 'Worker update' : 'Awaiting update'], [elapsed(j.elapsed_seconds), ''], [statusLabel(j), `Review: ${label(j.review_state)}`]];
      values.forEach(([primary, secondary], i) => { cells[i + 1].firstChild.textContent = primary; cells[i + 1].lastChild.textContent = secondary; });
      cells[5].firstChild.prepend(dot(tone(j)));
    });
  }
  function selectProject(id) {
    if (state.project === id) return;
    $('notice').textContent = '';
    state.project = id;
    const p = selectedProject();
    state.agent = null;
    state.feedback = []; clearInspector(); renderOverview(); loadDetail();
  }
  function selectAgent(id) {
    if (state.agent === id) return;
    $('notice').textContent = '';
    state.agent = id; clearInspector(); renderOverview(); loadDetail();
  }
  function selectOrchestrator() {
    $('notice').textContent = '';
    if (state.agent !== null) { state.agent = null; clearInspector(); renderOverview(); }
    setTab('messages'); loadDetail();
  }
  function clearInspector() {
    state.generation++; state.detail = null;
    ['timeline', 'questions', 'history', 'summary', 'files', 'tests', 'usage', 'stdout', 'stderr'].forEach(id => $(id).replaceChildren());
    $('detail-status').textContent = 'Loading local details…';
    $('inspector-title').textContent = state.agent ? 'Loading agent…' : 'Project orchestrator';
    $('inspector-subtitle').textContent = selectedProject()?.name || 'No project selected';
  }
  function setTab(tab, focus = false) {
    state.tab = tab;
    for (const button of document.querySelectorAll('[data-tab]')) {
      const active = button.dataset.tab === tab;
      button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !active;
      if (active && focus) button.focus();
    }
  }
  function renderTimeline(events) {
    $('timeline').replaceChildren();
    if (!events.length) { $('timeline').append(node('li', 'No progress messages yet.', 'empty')); return; }
    for (const event of events.slice(-8)) {
      const item = node('li');
      const content = node('div');
      content.append(node('strong', label(event.phase || event.type)), node('p', event.message || event.question || (event.verdict ? `Review: ${label(event.verdict)}` : event.state ? label(event.state) : event.source === 'worker' ? 'Worker update' : 'Runner lifecycle event')));
      item.append(node('time', when(event.at)), node('span', null, 'track'), content);
      $('timeline').append(item);
    }
  }
  function renderHistory(records) {
    const parent = $('history'); parent.replaceChildren();
    if (!records.length) return;
    parent.append(node('h3', 'Answered & closed questions'));
    for (const record of [...records].reverse()) {
      const article = node('article');
      article.append(node('small', `${record.source === 'orchestrator' ? 'Project orchestrator' : 'Worker'} · ${label(record.state)} · ${when(record.answered_at || record.expired_at || record.created_at)}`), node('p', record.question), node('p', record.answer || 'Closed without an answer.'));
      parent.append(article);
    }
  }
  function questionRoute(q) {
    const prefix = `/api/projects/${state.project}`;
    return q.source === 'orchestrator' ? `${prefix}/feedback/${q.id}/answer` : `${prefix}/jobs/${state.agent}/questions/${q.id}/answer`;
  }
  function renderQuestions(records) {
    const parent = $('questions');
    const pendingKeys = new Set(records.map(questionRoute));
    // An answer from another tab never destroys a focused draft.
    const held = [...parent.children].filter(el => !pendingKeys.has(el.dataset.key) && el.contains(document.activeElement));
    for (const el of held) { el.querySelector('button').disabled = true; el.querySelector('.result').textContent = 'This question is no longer pending. Your draft is preserved.'; }
    const existing = new Map([...parent.children].map(el => [el.dataset.key, el]));
    for (const q of records) {
      const route = questionRoute(q);
      let form = existing.get(route);
      if (!form) {
        form = node('form', null, 'question'); form.dataset.key = route;
        const header = node('div', null, 'question-header'); const heading = node('h3', 'Clarification needed'); heading.prepend(dot('amber'));
        header.append(heading, node('time', when(q.created_at)));
        const source = `${q.source === 'orchestrator' ? 'Project orchestrator' : 'Worker · ' + ($('inspector-title').textContent)} · ${selectedProject()?.name || ''}`;
        const inputId = `answer-${q.source}-${q.id}`;
        const inputLabel = node('label', 'Your answer'); inputLabel.htmlFor = inputId;
        const input = node('textarea'); input.id = inputId; input.name = 'answer'; input.required = true; input.maxLength = 60000;
        input.value = state.drafts.get(route) || '';
        input.addEventListener('input', () => state.drafts.set(route, input.value));
        const submit = node('button', 'Send answer'); submit.type = 'submit';
        const result = node('p', '', 'result'); result.setAttribute('role', 'status');
        form.append(header, node('p', source, 'source-label'), node('p', q.question, 'question-text'));
        if (q.context) form.append(node('p', q.context, 'context'));
        form.append(inputLabel, input, submit, result);
        form.addEventListener('submit', async event => {
          event.preventDefault();
          if (!input.value.trim()) { result.textContent = 'Enter an answer before sending.'; input.focus(); return; }
          const answer = input.value; submit.disabled = true; result.textContent = 'Saving your answer…';
          try {
            await api(route, answer); state.drafts.delete(route);
            result.textContent = 'Answer saved. The agent can continue.'; input.readOnly = true;
            $('notice').textContent = 'Answer saved locally.';
            // Keep the successful form visible and focused until the next deliberate selection.
            form.dataset.answered = 'true'; form.querySelector('h3').textContent = 'Answer saved';
            await refresh();
          } catch (error) { result.textContent = error.message; submit.disabled = false; }
        });
        parent.append(form);
      }
      existing.delete(route);
    }
    for (const form of existing.values()) if (!held.includes(form) && form.dataset.answered !== 'true') form.remove();
  }
  function renderInspector() {
    const p = selectedProject(); const j = state.detail;
    const isWorker = Boolean(state.agent);
    $('inspector-title').textContent = j ? agentName(j) : 'Project orchestrator';
    $('inspector-title').prepend(dot(j ? tone(j) : p?.pending_feedback.length ? 'amber' : 'neutral'));
    $('inspector-subtitle').textContent = `${j?.role || (isWorker ? 'Worker' : 'Project decisions')} · ${p?.name || ''}`;
    $('local-logs').hidden = !j;
    $('detail-status').textContent = '';
    if (j) {
      renderTimeline(j.events);
      const filePanel = $('files'); filePanel.replaceChildren(node('p', j.files === null ? 'Changed files unavailable for this workspace.' : `${j.files.length} changed files relative to the recorded baseline.`));
      if (j.files?.length) { const list = node('ul'); j.files.forEach(file => list.append(node('li', file))); filePanel.append(list); }
      const tests = $('tests'); tests.replaceChildren(node('p', `Review: ${label(j.review_state)}`));
      const evidence = j.review?.tests || [];
      if (evidence.length) { const list = node('ul'); evidence.forEach(test => list.append(node('li', test))); tests.append(list, node('p', `Recorded by ${j.review.reviewer} · ${when(j.review.reviewed_at)}`, 'muted')); }
      else tests.append(node('p', 'No independent test evidence recorded. Test counts are not inferred from logs.', 'muted'));
      const usage = $('usage'); usage.replaceChildren(node('p', j.usage ? 'Latest provider-reported snapshot. This may not represent cumulative usage.' : 'Usage is unavailable. This CLI has not reported token or cost data.', 'muted'));
      if (j.usage) { const list = node('dl'); Object.entries(j.usage).filter(([key]) => key !== 'provider_reported').forEach(([key, value]) => list.append(node('dt', label(key)), node('dd', count(value)))); usage.append(list); }
      $('stdout').textContent = j.logs.stdout || 'No output yet.'; $('stderr').textContent = j.logs.stderr || 'No errors reported.';
      renderQuestions(j.questions.filter(q => q.state === 'pending').map(q => ({ ...q, source: 'worker' })));
      renderHistory(j.questions.filter(q => q.state !== 'pending'));
      const head = node('div', null, 'summary-head'); head.append(node('h3', 'Summary'), node('small', `Elapsed ${elapsed(j.elapsed_seconds)}`));
      const grid = node('div', null, 'summary-grid');
      const tokens = j.usage?.total_tokens ?? (j.usage?.input_tokens !== undefined && j.usage?.output_tokens !== undefined ? j.usage.input_tokens + j.usage.output_tokens : null);
      grid.append(metric('Files changed', count(j.changed_files), 'From baseline'), metric('Test evidence', evidence.length || '—', evidence.length ? 'Review records' : 'Not reported'), metric('Token usage', count(tokens), j.usage?.total_cost_usd !== undefined ? `$${j.usage.total_cost_usd}` : 'Cost unavailable'));
      $('summary').replaceChildren(head, grid);
    } else {
      $('timeline').replaceChildren(node('li', p?.pending_feedback.length ? 'The project orchestrator is waiting for your input.' : 'No pending orchestrator questions.', 'empty'));
      for (const id of ['files', 'tests', 'usage']) $(id).replaceChildren(node('p', 'Select a worker agent to inspect these details.', 'muted'));
      renderQuestions(p?.pending_feedback || []); renderHistory(state.feedback);
      $('summary').replaceChildren();
    }
  }
  async function loadDetail() {
    const generation = ++state.generation;
    const project = state.project; const agent = state.agent;
    if (!project) { clearInspector(); $('detail-status').textContent = 'Select a project to begin.'; return; }
    try {
      const data = await api(`/api/projects/${project}${agent ? '/jobs/' + agent : ''}`);
      if (generation !== state.generation) return;
      if (agent) state.detail = data;
      else { state.detail = null; state.feedback = data.feedback_history; const p = selectedProject(); if (p) p.pending_feedback = data.pending_feedback; }
      renderInspector();
    } catch (error) { if (generation === state.generation) $('detail-status').textContent = error.message; }
  }
  async function refresh() {
    if (state.refreshing) return;
    state.refreshing = true;
    try {
      const data = await api('/api/overview'); state.projects = data.projects;
      if (!selectedProject()) {
        state.project = state.projects[0]?.id || null;
        state.agent = null;
        clearInspector();
      } else if (state.agent && !selectedProject().jobs.some(j => j.job_id === state.agent)) {
        state.agent = null; clearInspector();
      }
      renderOverview(); await loadDetail();
      $('connection').replaceChildren(dot('green'), document.createTextNode(state.paused ? 'Refresh paused' : 'Connected locally'));
      $('refresh-status').textContent = state.paused ? 'Auto-refresh paused' : 'Refreshes every 2 seconds';
      if (data.unreadable_records) $('notice').textContent = `${data.unreadable_records} unreadable local records were skipped.`;
    } catch (error) {
      $('notice').textContent = error.message;
      $('connection').replaceChildren(dot('amber'), document.createTextNode('Connection unavailable'));
    } finally { state.refreshing = false; }
  }
  $('filter').addEventListener('change', event => { state.filter = event.target.value; renderOverview(); });
  $('pause').addEventListener('click', () => {
    state.paused = !state.paused; $('pause').setAttribute('aria-pressed', String(state.paused));
    $('pause').textContent = state.paused ? 'Resume refresh' : 'Pause refresh';
    $('refresh-status').textContent = state.paused ? 'Auto-refresh paused' : 'Refreshes every 2 seconds';
    $('connection').replaceChildren(dot(state.paused ? 'neutral' : 'green'), document.createTextNode(state.paused ? 'Refresh paused' : 'Connected locally'));
    if (!state.paused) refresh();
  });
  $('show-messages').addEventListener('click', () => setTab('messages', true));
  const tabs = [...document.querySelectorAll('[data-tab]')];
  tabs.forEach((button, index) => {
    button.addEventListener('click', () => setTab(button.dataset.tab));
    button.addEventListener('keydown', event => {
      const target = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
      if (target !== null) { event.preventDefault(); setTab(tabs[target].dataset.tab, true); }
    });
  });
  function updateClock() { $('clock').textContent = new Date().toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }) + '\n' + when(new Date().toISOString()); }
  updateClock(); refresh();
  setInterval(() => { updateClock(); if (!state.paused) refresh(); }, 2000);
})();
