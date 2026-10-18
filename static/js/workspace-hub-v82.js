/* R82 — Unified Workspace index; existing project.js owns Project CRUD.
   Uses the existing Guide listing API and never modifies persisted content. */
(() => {
  'use strict';
  const root = document.getElementById('workspace-home');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const make = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  };
  const search = $('#project-search');
  const category = $('#ws-guide-category');
  const guideList = $('#ws-guide-list');
  const guideCount = $('#ws-guide-count');
  const choices = [...root.querySelectorAll('[data-ws-tab]')];
  let guides = [];
  let loaded = false;

  function normalize(value) { return String(value ?? '').toLocaleLowerCase(); }

  function makeGuideCard(guide) {
    const link = make('a', 'ws-guide-card');
    link.href = '/guide/' + encodeURIComponent(guide.id);
    const top = make('div', 'ws-guide-card-top');
    const kind = guide.content_format === 'documentation' ? 'Documentation' : 'Step-by-step';
    top.append(make('span', 'ws-guide-card-type', kind), make('span', 'ws-guide-card-category', guide.category || 'Other'));
    link.append(top, make('h3', '', guide.title || 'Untitled guide'));
    const description = String(guide.description || '').trim();
    if (description) link.append(make('p', '', description));
    const foot = make('div', 'ws-guide-card-foot');
    foot.append(make('span', 'ws-guide-card-author', guide.author ? 'By ' + guide.author : 'Team guide'));
    const status = make('span', 'ws-guide-card-status', guide.status || 'Draft');
    status.title = guide.updated_at ? 'Updated ' + guide.updated_at : '';
    foot.append(status);
    link.append(foot);
    link.setAttribute('aria-label', 'Open guide: ' + (guide.title || 'Untitled guide'));
    return link;
  }

  function renderGuides() {
    guideList.replaceChildren();
    if (!loaded) {
      guideList.append(make('p', 'ws-empty', 'Loading documentation…'));
      return;
    }
    const query = normalize(search.value.trim());
    const filter = category.value;
    const matching = guides.filter(guide =>
      (!filter || guide.category === filter) &&
      [guide.title, guide.description, guide.category, guide.author, guide.status]
        .some(value => normalize(value).includes(query))
    );
    if (!matching.length) {
      guideList.append(make('p', 'ws-empty', guides.length ?
        'No guides match your search or category.' : 'No guides yet. Create a guide to get started.'));
    } else {
      matching.forEach(guide => guideList.append(makeGuideCard(guide)));
    }
    guideCount.textContent = 'Showing ' + matching.length + ' of ' + guides.length + ' guides';
  }

  async function loadGuides() {
    loaded = false;
    renderGuides();
    try {
      const response = await fetch('/api/guides', {credentials:'same-origin', cache:'no-store'});
      if (!response.ok) throw new Error('Could not load guides (HTTP ' + response.status + ')');
      const data = await response.json();
      if (!data.ok || !Array.isArray(data.guides)) throw new Error(data.error || 'Invalid guides response');
      guides = data.guides;
      const selected = category.value;
      const categories = [...new Set(guides.map(guide => String(guide.category || 'Other')))]
        .sort((a, b) => a.localeCompare(b));
      category.replaceChildren();
      const all = make('option', '', 'All categories');
      all.value = '';
      category.append(all);
      categories.forEach(name => {
        const option = make('option', '', name);
        option.value = name;
        category.append(option);
      });
      category.value = categories.includes(selected) ? selected : '';
      loaded = true;
      renderGuides();
    } catch (error) {
      guideList.replaceChildren();
      const box = make('div', 'ws-empty');
      box.append(make('span', '', 'Documentation unavailable. ' + error.message + ' '));
      const retry = make('button', 'ws-view-all', 'Retry');
      retry.type = 'button';
      retry.addEventListener('click', loadGuides);
      box.append(retry);
      guideList.append(box);
      guideCount.textContent = '';
    }
  }

  function setTab(value, updateUrl = true) {
    const tab = ['projects', 'guides'].includes(value) ? value : 'all';
    root.dataset.wsTab = tab;
    choices.forEach(button => {
      const selected = button.dataset.wsTab === tab;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    $('#ws-project-panel').hidden = tab === 'guides';
    $('#ws-guide-panel').hidden = tab === 'projects';
    if (updateUrl) {
      const url = new URL(location.href);
      if (tab === 'all') url.searchParams.delete('tab');
      else url.searchParams.set('tab', tab);
      history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
  }

  choices.forEach((button, index) => {
    button.addEventListener('click', () => setTab(button.dataset.wsTab));
    button.addEventListener('keydown', event => {
      let next = index;
      if (event.key === 'ArrowRight') next = (index + 1) % choices.length;
      else if (event.key === 'ArrowLeft') next = (index + choices.length - 1) % choices.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = choices.length - 1;
      else return;
      event.preventDefault();
      const target = choices[next];
      setTab(target.dataset.wsTab);
      target.focus();
    });
  });
  root.querySelectorAll('[data-ws-show]').forEach(button =>
    button.addEventListener('click', () => {
      setTab(button.dataset.wsShow);
      search.focus();
    })
  );

  search.addEventListener('input', renderGuides);
  category.addEventListener('change', renderGuides);
  const create = $('#ws-create');
  document.addEventListener('click', event => {
    if (create.open && !create.contains(event.target)) create.open = false;
  });
  $('#new-project').addEventListener('click', () => { create.open = false; });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') create.open = false;
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) void loadGuides();
  });
  setTab(new URL(location.href).searchParams.get('tab'), false);
  void loadGuides();
})();
