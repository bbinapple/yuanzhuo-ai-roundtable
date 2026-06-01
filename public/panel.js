// ── YuanPanel ────────────────────────────────────────────────────────────
// Instantiable panel class for the 圆桌会议 web app.
// suffix: '' for single panel, 'L' for left dual panel, 'R' for right dual panel.
class YuanPanel {
  constructor(suffix) {
    this.suffix = suffix || '';
    // Migrated globals → instance properties
    this.bufs = {};
    this.timers = {};
    this.curKey = null;
    this.es = null;
    this.curSid = null;
    this.secBuf = '';
    this.secEl = null;
    this.secPanel = null;
    this.researchBuf = '';
    this.researchEl = null;
    this.researchPanel = null;
    this.pendingAttach = [];
    this.pendingResumeIdx = null;
    this.curTopicIdx = null;
    this.isPaused = false;
    this.pausedEvents = [];
    this.sessionRoles = {
      a: { name: '分析型辩手', icon: '🧠', cls: 'c' },
      b: { name: '执行型辩手', icon: '⚡', cls: 'x' },
      c: null,
    };
    // DOM refs (lazy-initialized after DOM is inserted)
    this.chatLog = null;
    this.statusBar = null;
    this.ta = null;
  }

  // DOM ID helper: append suffix when present
  $(id) {
    return document.getElementById(this.suffix ? id + '_' + this.suffix : id);
  }

  // Lazy-init DOM references; safe to call multiple times
  _initDom() {
    if (!this.chatLog) {
      this.chatLog = this.$('chat-log');
      this.statusBar = this.$('status-bar');
      this.ta = this.$('topic-input');
    }
  }

  updateHeaderFromSelects() {
    this._initDom();
    const selA = this.$('role-a-select');
    const selB = this.$('role-b-select');
    const selC = this.$('role-c-select');
    const shortA = selA.value;
    const shortB = selB.value;
    const shortC = selC ? selC.value : '';
    const rA = allRoles.find(r => r.short === shortA) || allRoles[0];
    const rB = allRoles.find(r => r.short === shortB) || allRoles[1];
    const rC = allRoles.find(r => r.short === shortC) || null;
    const optA = selA.options[selA.selectedIndex];
    const optB = selB.options[selB.selectedIndex];
    const optC = selC ? selC.options[selC.selectedIndex] : null;
    this.syncHeaderRoleSelect('a', selA);
    this.syncHeaderRoleSelect('b', selB);
    this.syncHeaderRoleSelect('c', selC);
    if (rA) {
      this.$('icon-a').textContent = rA.icon;
      this.$('name-a').textContent = rA.name;
      this.$('meta-a').textContent = `${rA.icon} ${rA.name}`;
      const subtitleA = (optA && optA.dataset.subtitle) || rA.subtitle || '';
      const tagElA = this.$('role-a-tag');
      if (tagElA) tagElA.textContent = subtitleA;
    }
    if (rB) {
      this.$('icon-b').textContent = rB.icon;
      this.$('name-b').textContent = rB.name;
      this.$('meta-b').textContent = `${rB.name} ${rB.icon}`;
      const subtitleB = (optB && optB.dataset.subtitle) || rB.subtitle || '';
      const tagElB = this.$('role-b-tag');
      if (tagElB) tagElB.textContent = subtitleB;
    }
    const iconC = this.$('icon-c');
    const nameC = this.$('name-c');
    const tagC = this.$('role-c-tag');
    if (iconC && nameC && tagC) {
      if (rC) {
        iconC.textContent = rC.icon || '🔎';
        nameC.textContent = rC.name || '第三辩手';
        tagC.textContent = (optC && optC.dataset.subtitle) || rC.subtitle || '第三视角';
      } else {
        iconC.textContent = '🔎';
        nameC.textContent = '无第三辩手';
        tagC.textContent = '未启用';
      }
    }
    this.sessionRoles.c = rC ? { ...rC, cls: 'm' } : null;
  }

  syncHeaderRoleSelect(side, sourceSelect) {
    const headerSelect = this.$(`header-role-${side}-select`);
    if (!headerSelect || !sourceSelect) return;
    if (headerSelect.dataset.sourceHtml !== sourceSelect.innerHTML) {
      headerSelect.innerHTML = sourceSelect.innerHTML;
      headerSelect.dataset.sourceHtml = sourceSelect.innerHTML;
    }
    headerSelect.value = sourceSelect.value;
  }

  chooseHeaderRole(side, value) {
    const selectId = side === 'c' ? 'role-c-select' : (side === 'b' ? 'role-b-select' : 'role-a-select');
    const sourceSelect = this.$(selectId);
    if (!sourceSelect) return;
    sourceSelect.value = value;
    this.updateHeaderFromSelects();
  }

  setStatus(msg, type) {
    this._initDom();
    this.statusBar.textContent = msg;
    this.statusBar.className = `pill ${type}`;
    this.statusBar.style.display = msg ? 'block' : 'none';
  }

  _queueOrRun(fn) {
    if (this.isPaused) {
      this.pausedEvents.push(fn);
      return;
    }
    fn();
  }

  _updatePauseButton() {
    const btn = this.$('btn-pause');
    if (!btn) return;
    const active = !!this.es;
    btn.disabled = !active;
    btn.textContent = this.isPaused ? 'Resume / 继续' : 'Pause / 暂停';
    btn.title = this.isPaused ? 'Resume debate display / 继续显示当前辩论' : 'Pause debate display / 暂停显示当前辩论';
  }

  togglePause() {
    if (!this.es) return;
    this.isPaused = !this.isPaused;
    this._updatePauseButton();
    if (this.isPaused) {
      this.setStatus('⏸ Paused. New debate output will be buffered on this page. / 已暂停显示，新的辩论内容会暂存在本页。', 'warn');
      return;
    }
    const queued = this.pausedEvents.splice(0);
    queued.forEach(fn => fn());
    this.setStatus('▶️ Resumed debate display. / 已继续显示当前辩论。', 'info');
  }

  mkRow(role, round) {
    const isA = role === 'zhuge';
    const isB = role === 'sima';
    const info = isA ? this.sessionRoles.a : (isB ? this.sessionRoles.b : (this.sessionRoles.c || { name: '第三辩手', icon: '🔎', cls: 'm' }));
    const cls = isA ? 'c' : (isB ? 'x' : 'm');
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    const row = document.createElement('div');
    row.className = `mr${isB ? ' r' : ''}`;
    row.innerHTML = `
      <div class="ml">
        <span>${info.icon}</span>
        <span class="ml-name ${cls}">${info.name}</span>
        <span class="ml-meta">第 ${round} 轮 · ${time}</span>
      </div>
      <div class="bw">
        <div class="b ${cls} thinking" data-content>
          思考中 <div class="dots"><span></span><span></span><span></span></div>
        </div>
        <button class="btn-copy" title="复制">📋</button>
        <button class="btn-toggle" title="展开/折叠" style="display:none">⤵ 展开</button>
        <button class="btn-regen" title="Regenerate this turn / 重新生成这一轮" style="display:none">🔄 Regen</button>
      </div>`;
    row.querySelector('.btn-toggle').addEventListener('click', () => {
      const b2 = row.querySelector('[data-content]');
      const tb = row.querySelector('.btn-toggle');
      if (b2.classList.toggle('collapsed')) { tb.textContent = '⤵ 展开'; }
      else { tb.textContent = '⤴ 折叠'; }
    });
    // B3: 点击角色头像查看 system prompt
    const iconSpan = row.querySelector('.ml span');
    if (iconSpan) {
      iconSpan.style.cursor = 'pointer';
      iconSpan.title = '查看角色 prompt';
      iconSpan.addEventListener('click', () => {
        const short = isA ? this.$('role-a-select').value : (isB ? this.$('role-b-select').value : this.$('role-c-select').value);
        if (typeof showRoleModal === 'function') showRoleModal(short);
      });
    }
    row.querySelector('.btn-regen').addEventListener('click', () => this._regenLast(row));
    row.querySelector('.btn-copy').addEventListener('click', async () => {
      const key = row.dataset.key;
      const text = key ? (this.bufs[key] || '') : '';
      if (!text) return;
      const btn = row.querySelector('.btn-copy');
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = '✅';
      } catch {
        btn.textContent = '❌';
      }
      setTimeout(() => { btn.textContent = '📋'; }, 1200);
    });
    return row;
  }

  render(key, final) {
    this._initDom();
    const row = this.chatLog.querySelector(`[data-key="${key}"]`);
    if (!row) return;
    const b = row.querySelector('[data-content]');
    const text = this.bufs[key] || '';
    if (!text) return;
    b.classList.remove('thinking', 'streaming');
    let html = marked.parse(text);
    if (text.includes('[散会]')) html = html.replace(/\[散会\]/g, '<span class="adj">✓ 达成共识</span>');
    if (text.includes('[ASK_USER]')) html = html.replace(/\[ASK_USER\]/g, '<span class="adj">💬 Ask user</span>');
    b.innerHTML = html;
    this._attachCodeCopyButtons(b);
    if (!final) b.classList.add('streaming');
    if (final && text.length > 200) {
      b.classList.add('collapsed');
      const tb = row.querySelector('.btn-toggle');
      if (tb) { tb.style.display = ''; tb.textContent = '⤵ 展开'; }
    }
    row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  sched(key) {
    if (this.timers[key]) clearTimeout(this.timers[key]);
    this.timers[key] = setTimeout(() => this.render(key, false), 60);
  }

  addToolRow(text, isCalling) {
    this._initDom();
    const el = document.createElement('div');
    el.className = `tool-row${isCalling ? ' calling' : ''}`;
    el.textContent = text;
    this.chatLog.appendChild(el);
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return el;
  }

  initResearchPanel() {
    this._initDom();
    const panel = document.createElement('div');
    panel.className = 'research-panel';
    panel.innerHTML = '<div class="research-label">📚 Preheat Research / 议题预热</div>';
    const b = document.createElement('div');
    b.className = 'b research-body streaming';
    b.setAttribute('data-research', '1');
    panel.appendChild(b);
    this.chatLog.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    this.researchPanel = panel;
    return b;
  }

  addModeratorStrip(text) {
    this._initDom();
    const el = document.createElement('div');
    el.className = 'moderator-strip';
    el.textContent = `🎙 ${text}`;
    this.chatLog.appendChild(el);
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  initSecPanel() {
    this._initDom();
    const panel = document.createElement('div');
    panel.className = 'sec-panel';
    panel.innerHTML = '<div class="sec-label">📝 Secretary Summary / 秘书总结</div>';
    const b = document.createElement('div');
    b.className = 'b sec streaming';
    b.setAttribute('data-sec', '1');
    panel.appendChild(b);
    this.chatLog.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    this.secPanel = panel;
    return b;
  }

  addSecActions(sid, exportPath) {
    if (!this.secPanel) return;
    const actDiv = document.createElement('div');
    actDiv.className = 'sec-actions';

    const btnExport = document.createElement('button');
    btnExport.className = 'btn-export';
    btnExport.innerHTML = '💾 Export MD / 导出 MD';
    btnExport.onclick = () => this.downloadExport(sid);
    actDiv.appendChild(btnExport);

    this.secPanel.appendChild(actDiv);
  }

  async downloadExport(sid) {
    try {
      const resp = await fetch(`/api/export/${sid}`);
      if (!resp.ok) { alert('Export failed / 导出失败：' + resp.statusText); return; }
      const blob = await resp.blob();
      const cd = resp.headers.get('Content-Disposition') || '';
      const fnMatch = cd.match(/filename="([^"]+)"/);
      const filename = fnMatch ? fnMatch[1] : `yuanzhuo-export.md`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) { alert('Export failed / 导出失败：' + e.message); }
  }

  async launchLocalAssistant(sid, btn) {
    btn.disabled = true;
    btn.textContent = '启动中…';
    try {
      const resp = await fetch(`/api/launch_local_assistant/${sid}`, { method: 'POST' });
      const data = await resp.json();
      if (data.ok) {
        btn.textContent = '✅ 已启动 Terminal';
      } else {
        btn.textContent = '启动失败';
        const hint = document.createElement('div');
        hint.style.cssText = 'font-size:11px;color:var(--dim);margin-top:6px';
        hint.textContent = '请手动运行：' + (data.cmd || '');
        this.secPanel.appendChild(hint);
      }
    } catch (e) {
      btn.textContent = '启动失败';
    }
  }

  addTodosPanel(todos, topicIdx) {
    if (!this.secPanel || !todos || !todos.length) return;
    const panel = document.createElement('div');
    panel.className = 'todos-panel';
    panel.innerHTML = `<div class="todos-label">📋 待办清单（${todos.length} 项）</div>`;
    todos.forEach((todo, j) => {
      const item = document.createElement('div');
      item.className = 'todo-item';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'todo-cb';
      cb.checked = todo.status === 'done';
      const textEl = document.createElement('span');
      textEl.className = `todo-text${todo.status === 'done' ? ' done-text' : ''}`;
      textEl.textContent = todo.text;
      cb.addEventListener('change', () => this.toggleTodo(topicIdx, j, cb, textEl));
      item.appendChild(cb);
      item.appendChild(textEl);
      panel.appendChild(item);
    });
    this.secPanel.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async toggleTodo(topicIdx, todoIdx, cb, textEl) {
    const newStatus = cb.checked ? 'done' : 'pending';
    try {
      await fetch(`/api/todos/${topicIdx}/${todoIdx}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      if (newStatus === 'done') {
        textEl.classList.add('done-text');
      } else {
        textEl.classList.remove('done-text');
      }
      loadHistory();
    } catch (e) {
      cb.checked = !cb.checked;
    }
  }

  addTagsRow(tags) {
    if (!this.secPanel || !tags || !tags.length) return;
    const row = document.createElement('div');
    row.className = 'tags-row';
    tags.forEach(tag => {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.textContent = tag;
      row.appendChild(chip);
    });
    this.secPanel.appendChild(row);
  }

  addScoreCard(scores) {
    if (!this.secPanel) return;
    const card = document.createElement('div');
    card.className = 'score-card';
    card.innerHTML = `
      <div class="score-card-title">本场评分</div>
      <div class="score-row">
        <div class="score-item">🧠 深度 <span class="score-num">${scores.depth}/5</span></div>
        <div class="score-item">🤝 共识 <span class="score-num">${scores.consensus}/5</span></div>
        <div class="score-item">⚡ 执行 <span class="score-num">${scores.execution}/5</span></div>
      </div>
      <div class="score-comment">"${scores.comment || ''}"</div>`;
    this.secPanel.appendChild(card);
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  showInsertPanel(sid) {
    this._initDom();
    const panel = document.createElement('div');
    panel.className = 'ask-user';
    panel.setAttribute('data-ask', 'insert');
    const inputId = this.suffix ? `user-insert-input_${this.suffix}` : 'user-insert-input';
    const pVar = this.suffix ? `p${this.suffix}` : 'p';
    panel.innerHTML = `
      <div class="ask-user-label">💬 AI invites your optional comment / AI 邀请你发言（可选）</div>
      <input type="text" id="${inputId}" placeholder="Type your comment, press Enter to submit / 输入你的发言，回车提交..." />
      <div class="ask-user-btns">
        <button class="btn-stage primary" onclick="${pVar}.submitInsert('${sid}')">Submit / 提交</button>
        <button class="btn-stage neutral" onclick="${pVar}.skipInsert('${sid}')">Skip / 跳过</button>
      </div>`;
    this.chatLog.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const inp = panel.querySelector('input');
    inp.focus();
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.submitInsert(sid); }
    });
  }

  submitInsert(sid) {
    this._initDom();
    const panel = this.chatLog.querySelector('[data-ask="insert"]');
    const val = panel ? panel.querySelector('input').value.trim() : '';
    this.removeAskPanel();
    if (val) {
      const userRow = document.createElement('div');
      userRow.className = 'mr';
      const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
      userRow.innerHTML = `
        <div class="ml">
          <span>👤</span>
          <span class="ml-name user">USER</span>
          <span class="ml-meta">${time}</span>
        </div>
        <div class="bw">
          <div class="b user">${val}</div>
        </div>`;
      this.chatLog.appendChild(userRow);
      userRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    this.postRespond(sid, { kind: 'insert', value: val, text: val });
  }

  skipInsert(sid) {
    this.removeAskPanel();
    this.postRespond(sid, { kind: 'insert', value: '', text: '' });
  }

  showStagePanel(sid) {
    this._initDom();
    const panel = document.createElement('div');
    panel.className = 'ask-user';
    panel.setAttribute('data-ask', 'stage');
    const insertAreaId = this.suffix ? `stage-insert-area_${this.suffix}` : 'stage-insert-area';
    const insertInputId = this.suffix ? `stage-insert-input_${this.suffix}` : 'stage-insert-input';
    const pVar = this.suffix ? `p${this.suffix}` : 'p';
    panel.innerHTML = `
      <div class="ask-user-label">⏸ Stage pause - choose next step / 阶段暂停 - 请选择</div>
      <div class="ask-user-btns">
        <button class="btn-stage primary" onclick="${pVar}.stageChoice('${sid}','continue')">① Continue debate / 继续辩</button>
        <button class="btn-stage danger" onclick="${pVar}.stageChoice('${sid}','stop')">② End and summarize / 散会出总结</button>
        <button class="btn-stage warn" onclick="${pVar}.showStageInsert('${sid}')">③ Add my comment / 我插一句</button>
      </div>
      <div id="${insertAreaId}" style="display:none;margin-top:10px">
        <input type="text" id="${insertInputId}" placeholder="Your comment / 你的发言..." style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:rgba(255,252,240,.9);font-family:inherit;font-size:13px;outline:none"/>
        <div class="ask-user-btns" style="margin-top:8px">
          <button class="btn-stage primary" onclick="${pVar}.submitStageInsert('${sid}')">Submit / 提交</button>
        </div>
      </div>`;
    this.chatLog.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  showStageInsert(sid) {
    const area = this.$('stage-insert-area');
    if (area) { area.style.display = 'block'; this.$('stage-insert-input').focus(); }
  }

  stageChoice(sid, value) {
    this.removeAskPanel();
    this.postRespond(sid, { kind: 'stage', value: value, text: '' });
  }

  submitStageInsert(sid) {
    const inp = this.$('stage-insert-input');
    const txt = inp ? inp.value.trim() : '';
    this.removeAskPanel();
    this.postRespond(sid, { kind: 'stage', value: 'insert', text: txt });
  }

  showToolConfirmPanel(sid, name, args, count) {
    this._initDom();
    const panel = document.createElement('div');
    panel.className = 'ask-user';
    panel.setAttribute('data-ask', 'tool_confirm');
    let argsStr = '';
    try { const a = JSON.parse(args); argsStr = Object.values(a).join(', '); } catch (e) { argsStr = args; }
    const pVar = this.suffix ? `p${this.suffix}` : 'p';
    panel.innerHTML = `
      <div class="ask-user-label">⚠️ AI 想第 ${count} 次调工具：${name}("${argsStr}")</div>
      <div class="ask-user-btns">
        <button class="btn-stage primary" onclick="${pVar}.confirmTool('${sid}','y')">允许</button>
        <button class="btn-stage danger" onclick="${pVar}.confirmTool('${sid}','n')">拒绝</button>
      </div>`;
    this.chatLog.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  confirmTool(sid, value) {
    this.removeAskPanel();
    this.postRespond(sid, { kind: 'tool_confirm', value: value });
  }

  removeAskPanel() {
    this._initDom();
    const el = this.chatLog.querySelector('[data-ask]');
    if (el) el.remove();
  }

  async postRespond(sid, payload) {
    try {
      await fetch(`/api/respond/${sid}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) { console.error('respond failed', e); }
  }

  triggerFileInput() {
    this.$('file-input').click();
  }

  async handleFiles(files) {
    for (const file of files) {
      const ext = '.' + file.name.split('.').pop().toLowerCase();
      const isImg = file.type.startsWith('image/');
      const isPdf = file.type === 'application/pdf' || ext === '.pdf';

      if (isPdf) {
        if (file.size > ATTACH_PDF_MAX) {
          alert(`PDF ${file.name}（${(file.size / 1024 / 1024).toFixed(1)}MB）超过 5MB，已跳过`);
          continue;
        }
        const dataUrl = await readAsDataURL(file);
        const b64 = dataUrl.split(',')[1];
        this.pendingAttach.push({ kind: 'pdf', name: file.name, data: b64 });
      } else if (isImg) {
        if (file.size > ATTACH_IMG_MAX) {
          alert(`图片 ${file.name}（${(file.size / 1024 / 1024).toFixed(1)}MB）超过 5MB，已跳过`);
          continue;
        }
        const dataUrl = await readAsDataURL(file);
        this.pendingAttach.push({ kind: 'img', name: file.name, data: dataUrl });
      } else if (TEXT_EXTS.has(ext)) {
        let text = await readAsText(file);
        if (text.length > ATTACH_TXT_MAX) text = text.slice(0, ATTACH_TXT_MAX) + '\n\n... (内容已截断)';
        this.pendingAttach.push({ kind: 'txt', name: file.name, data: text });
      }
    }
    this.renderAttachPreview();
    this.$('file-input').value = '';
  }

  renderAttachPreview() {
    const preview = this.$('attach-preview');
    preview.innerHTML = '';
    const pVar = this.suffix ? `p_${this.suffix}` : 'p';
    this.pendingAttach.forEach((a, i) => {
      const chip = document.createElement('div');
      chip.className = 'attach-chip';
      const icon = a.kind === 'img' ? '🖼' : (a.kind === 'pdf' ? '📕' : '📄');
      chip.innerHTML = `${icon} ${a.name} <span class="rm" onclick="${pVar}.removeAttach(${i})">✕</span>`;
      preview.appendChild(chip);
    });
  }

  removeAttach(i) {
    this.pendingAttach.splice(i, 1);
    this.renderAttachPreview();
  }

  async startRound() {
    this._initDom();
    const topicRaw = this.ta.value.trim();
    if (!topicRaw) { this.ta.focus(); return; }
    if (typeof requireUserApiKey === 'function' && !requireUserApiKey()) return;
    if (this.es) { this.es.close(); this.es = null; }

    const template = this.$('template-select').value;
    const roleAShort = this.$('role-a-select').value;
    const roleBShort = this.$('role-b-select').value;
    const roleCShort = this.$('role-c-select') ? this.$('role-c-select').value : '';
    const roleSecretaryShort = this.$('role-secretary-select') ? this.$('role-secretary-select').value : '';
    const researchChecked = this.$('research-toggle').checked;
    if (typeof getUnavailableRoleMessages === 'function') {
      const unavailable = getUnavailableRoleMessages([roleAShort, roleBShort, roleCShort].filter(Boolean));
      if (unavailable.length) {
        alert(`These agent models are currently unavailable / 以下角色模型当前不可用：\n${unavailable.join('\n')}\n\nPlease switch to available models or configure defaults. / 请先切换到可用模型或配置默认模型。`);
        return;
      }
    }

    const imageUrls = this.pendingAttach.filter(a => a.kind === 'img').map(a => a.data);
    const textBlocks = this.pendingAttach.filter(a => a.kind === 'txt')
      .map(a => `## 附件文件：${a.name}\n\`\`\`\n${a.data}\n\`\`\``);
    const attach = textBlocks.length ? '\n\n' + textBlocks.join('\n\n') : '';
    const pdfFiles = this.pendingAttach.filter(a => a.kind === 'pdf')
      .map(a => ({ name: a.name, data: a.data }));

    if (this.pendingResumeIdx === null) {
      const sep = document.createElement('div');
      sep.className = 'sep';
      const templateLabels = { free: 'Free Topic / 自由议题', selection: 'Ecommerce Review / 电商机会评估', sidehustle: 'Side Project / 副业方向', negotiation: 'Negotiation Prep / 谈判准备', swot: 'SWOT Decision / SWOT 决策' };
      const tLabel = template !== 'free' ? ` · ${templateLabels[template] || template}` : '';
      sep.innerHTML = `<span>📌 ${topicRaw}${tLabel}</span>`;
      this.chatLog.appendChild(sep);
    }

    this.bufs = {}; this.timers = {}; this.curKey = null;
    this.secBuf = ''; this.secEl = null; this.secPanel = null;
    this.researchBuf = ''; this.researchEl = null; this.researchPanel = null;
    this.curTopicIdx = null;

    const rA = allRoles.find(r => r.short === roleAShort) || { name: '分析型辩手', icon: '🧠' };
    const rB = allRoles.find(r => r.short === roleBShort) || { name: '执行型辩手', icon: '⚡' };
    const rC = allRoles.find(r => r.short === roleCShort) || null;
    this.sessionRoles = { a: { ...rA, cls: 'c' }, b: { ...rB, cls: 'x' }, c: rC ? { ...rC, cls: 'm' } : null };

    this.ta.value = ''; this.ta.style.height = 'auto';
    this.pendingAttach = []; this.renderAttachPreview();
    this.ta.disabled = true;
    this.$('btn-start').disabled = true;
    this.isPaused = false;
    this.pausedEvents = [];
    this._updatePauseButton();
    this.setStatus('Debaters are ready. Debate starting... / 辩手已就位，辩论开始…', 'info');

    const resumeId = (this.pendingResumeIdx !== null) ? `resume-by-topic-${this.pendingResumeIdx}` : '';
    this.pendingResumeIdx = null;

    let sid;
    try {
      const r = await fetch('/api/round', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: topicRaw,
          attach,
          image_urls: imageUrls,
          template,
          pdf_files: pdfFiles,
          role_a: roleAShort,
          role_b: roleBShort,
          role_c: roleCShort,
          role_secretary: roleSecretaryShort,
          custom_roles: typeof getCustomRoles === 'function' ? getCustomRoles() : [],
          api_settings: typeof getApiSettings === 'function' ? getApiSettings() : {},
          resume_session_id: resumeId,
          research: researchChecked ? 'on' : 'off',
          moderator: true,
          reasoning: 'high',
        }),
      });
      const rdata = await r.json();
      sid = rdata.session_id;
      this.curSid = sid;
      if (typeof loadHistory === 'function') loadHistory();
      if (rdata.pdf_names && rdata.pdf_names.length) {
        const info = document.createElement('div');
        info.className = 'pill info';
        info.style.display = 'block';
        info.textContent = `📕 已解析 PDF：${rdata.pdf_names.join('、')}`;
        this.chatLog.appendChild(info);
      }
    } catch (e) {
      this.setStatus('❌ Connection failed / 连接失败：' + e.message, 'error');
      this.unlock(); return;
    }

    this._connectSSE(sid);
  }

  // B1: SSE wiring 提取为独立方法，供 startRound 和 _regenLast 复用
  _connectSSE(sid) {
    if (this.es) { this.es.close(); this.es = null; }
    this.es = new EventSource(`/api/stream/${sid}`);
    this.isPaused = false;
    this.pausedEvents = [];
    this._updatePauseButton();

    this.es.addEventListener('roles', e => {
      this._queueOrRun(() => {
        const { role_a, role_b, role_c } = JSON.parse(e.data);
        this.sessionRoles.a = { ...role_a, cls: 'c' };
        this.sessionRoles.b = { ...role_b, cls: 'x' };
        this.sessionRoles.c = role_c ? { ...role_c, cls: 'm' } : null;
        this.$('icon-a').textContent = role_a.icon;
        this.$('name-a').textContent = role_a.name;
        this.$('icon-b').textContent = role_b.icon;
        this.$('name-b').textContent = role_b.name;
        this.updateHeaderFromSelects();
      });
    });

    this.es.addEventListener('research_start', e => {
      this._queueOrRun(() => this.setStatus('📚 Preheat research in progress... / 议题预热中…', 'info'));
    });

    this.es.addEventListener('research', e => {
      this._queueOrRun(() => {
        const { chunk } = JSON.parse(e.data);
        if (!this.researchEl) this.researchEl = this.initResearchPanel();
        this.researchBuf += chunk;
        this.researchEl.innerHTML = marked.parse(this.researchBuf);
        this._attachCodeCopyButtons(this.researchEl);
      });
    });

    this.es.addEventListener('research_tool', e => {
      this._queueOrRun(() => {
        const { summary } = JSON.parse(e.data);
        if (this.researchPanel) {
          const toolRow = document.createElement('div');
          toolRow.className = 'tool-row';
          toolRow.textContent = summary;
          this.researchPanel.appendChild(toolRow);
        }
      });
    });

    this.es.addEventListener('research_done', e => {
      this._queueOrRun(() => {
        if (this.researchEl) this.researchEl.classList.remove('streaming');
        this.setStatus('Agents are ready. Debate starting... / 辩论开始…', 'info');
      });
    });

    this.es.addEventListener('turn_open', e => {
      this._queueOrRun(() => {
        const { role, round } = JSON.parse(e.data);
        if (this.curKey) this.render(this.curKey, true);
        const row = this.mkRow(role, round);
        const key = `${role}-${round}-${Date.now()}`;
        row.dataset.key = key; this.bufs[key] = ''; this.curKey = key;
        this.chatLog.appendChild(row);
        row.scrollIntoView({ behavior: 'smooth', block: 'end' });
        this.$('dot-zhuge').classList.toggle('active', role === 'zhuge');
        this.$('dot-sima').classList.toggle('active', role === 'sima');
        const dotThird = this.$('dot-third');
        if (dotThird) dotThird.classList.toggle('active', role === 'third');
      });
    });

    ['zhuge', 'sima', 'third'].forEach(who => {
      this.es.addEventListener(who, e => {
        this._queueOrRun(() => {
          if (!this.curKey) return;
          this.bufs[this.curKey] += JSON.parse(e.data).chunk;
          this.sched(this.curKey);
        });
      });
    });

    this.es.addEventListener('tool_call', e => {
      this._queueOrRun(() => {
        const { name, args } = JSON.parse(e.data);
        let argStr = '';
        try { const a = JSON.parse(args); argStr = Object.values(a).slice(0, 1).join(''); } catch (ex) { argStr = args; }
        this.addToolRow(`🔧 调用 ${name}("${argStr.slice(0, 60)}")…`, true);
      });
    });

    this.es.addEventListener('tool_result', e => {
      this._queueOrRun(() => {
        const { summary } = JSON.parse(e.data);
        const rows = this.chatLog.querySelectorAll('.tool-row.calling');
        if (rows.length) rows[rows.length - 1].remove();
        this.addToolRow(summary, false);
      });
    });

    this.es.addEventListener('moderator', e => {
      this._queueOrRun(() => {
        const { text } = JSON.parse(e.data);
        if (this.curKey) { this.render(this.curKey, true); this.curKey = null; }
        if (text) this.addModeratorStrip(text);
      });
    });

    this.es.addEventListener('ask_user', e => {
      this._queueOrRun(() => {
        if (this.curKey) { this.render(this.curKey, true); this.curKey = null; }
        const d = JSON.parse(e.data);
        if (d.kind === 'insert') {
          this.showInsertPanel(sid);
        } else if (d.kind === 'stage') {
          this.showStagePanel(sid);
        } else if (d.kind === 'tool_confirm') {
          this.showToolConfirmPanel(sid, d.name, d.args, d.count);
        }
      });
    });

    this.es.addEventListener('secretary', e => {
      this._queueOrRun(() => {
        const { chunk } = JSON.parse(e.data);
        if (!this.secEl) this.secEl = this.initSecPanel();
        this.secBuf += chunk;
        this.secEl.innerHTML = marked.parse(this.secBuf);
        this._attachCodeCopyButtons(this.secEl);
      });
    });

    this.es.addEventListener('scores', e => {
      this._queueOrRun(() => {
        const scores = JSON.parse(e.data);
        this.addScoreCard(scores);
      });
    });

    this.es.addEventListener('todos', e => {
      this._queueOrRun(() => {
        const { todos } = JSON.parse(e.data);
        this.addTodosPanel(todos, this.curTopicIdx);
      });
    });

    this.es.addEventListener('tags', e => {
      this._queueOrRun(() => {
        const { tags } = JSON.parse(e.data);
        this.addTagsRow(tags);
      });
    });

    this.es.addEventListener('done', e => {
      this._queueOrRun(() => {
        const { duration_ms, turns, session_id, export_path, topic_idx } = JSON.parse(e.data);
        if (topic_idx !== null && topic_idx !== undefined) this.curTopicIdx = topic_idx;
        if (this.curKey) { this.render(this.curKey, true); this.curKey = null; }
        if (this.secEl) this.secEl.classList.remove('streaming');
        this.$('dot-zhuge').classList.remove('active');
        this.$('dot-sima').classList.remove('active');
        const dotThird = this.$('dot-third');
        if (dotThird) dotThird.classList.remove('active');
        this.setStatus(`✅ Done / 散会 · ${turns} turns / 轮 · ${(duration_ms / 1000).toFixed(0)}s`, 'done');
        this.addSecActions(session_id || sid, export_path);
        this.curSid = session_id || sid;
        this.es.close(); this.es = null;
        this.isPaused = false;
        this.pausedEvents = [];
        this.unlock();
        this._updatePauseButton();
        this._updateRegenButtons();
        if (typeof loadHistory === 'function') loadHistory();
      });
    });

    this.es.addEventListener('error', e => {
      this._queueOrRun(() => {
        let msg = 'Connection interrupted / 连接中断';
        try { if (e.data) msg = JSON.parse(e.data).message; } catch (_) { }
        if (this.es && this.es.readyState !== EventSource.CLOSED) this.setStatus('❌ ' + msg, 'error');
        if (this.es) { this.es.close(); this.es = null; }
        this.isPaused = false;
        this.pausedEvents = [];
        this.unlock();
        this._updatePauseButton();
      });
    });
  }

  // B1: 更新 🔄 按钮可见性 — 只在最后一条 AI 气泡上显示
  _updateRegenButtons() {
    this.chatLog.querySelectorAll('.btn-regen').forEach(b => b.style.display = 'none');
    if (!this.curSid) return;
    const allMr = this.chatLog.querySelectorAll('.mr');
    if (!allMr.length) return;
    const lastMr = allMr[allMr.length - 1];
    const btn = lastMr.querySelector('.btn-regen');
    if (btn) btn.style.display = '';
  }

  // B1: 重新生成最后一轮
  async _regenLast(row) {
    if (!this.curSid) { alert('Session expired; cannot regenerate. / Session 已失效，无法重新生成'); return; }
    if (!confirm('Regenerate the last turn? Current content will be replaced. / 重新生成最后这一轮？当前内容会被替换。')) return;
    try {
      const r = await fetch(`/api/regenerate_last/${this.curSid}`, { method: 'POST' });
      if (!r.ok) { alert('Regeneration failed / 重新生成失败：' + (await r.text())); return; }
      const { session_id: newSid } = await r.json();
      // 删除最后那条气泡
      if (row && row.parentNode) row.remove();
      // 重置 buffers
      this.bufs = {}; this.timers = {}; this.curKey = null;
      this.secBuf = ''; this.secEl = null; this.secPanel = null;
      // 锁住输入，更新状态
      this.ta.disabled = true;
      this.$('btn-start').disabled = true;
      this.setStatus('🔄 Regenerating... / 重新生成中…', 'info');
      // 连接新 SSE
      this.curSid = newSid;
      this._connectSSE(newSid);
    } catch (e) { alert('Network error / 网络错误：' + e.message); }
  }

  unlock() {
    this._initDom();
    this.ta.disabled = false;
    this.$('btn-start').disabled = false;
    this._updatePauseButton();
  }

  _attachCodeCopyButtons(container) {
    if (!container) return;
    container.querySelectorAll('pre').forEach(pre => {
      if (pre.querySelector('.btn-code-copy')) return;
      pre.style.position = 'relative';
      const btn = document.createElement('button');
      btn.className = 'btn-code-copy';
      btn.textContent = '📋';
      btn.title = '复制代码';
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const code = pre.querySelector('code')?.textContent || pre.textContent;
        try {
          await navigator.clipboard.writeText(code);
          btn.textContent = '✅';
        } catch { btn.textContent = '❌'; }
        setTimeout(() => { btn.textContent = '📋'; }, 1200);
      });
      pre.appendChild(btn);
    });
  }

  _buildCurrentMd() {
    this._initDom();
    const lines = [];
    const seps = this.chatLog.querySelectorAll('.sep span');
    const topic = seps.length ? seps[seps.length - 1].textContent.replace(/^📌\s*/, '').trim() : '当前会话';
    const ts = new Date().toLocaleString('zh-CN');
    lines.push(`# 议题：${topic}`, '', `*${ts} · 圆桌会议中途快照*`, '');
    const nameA = this.sessionRoles.a.name;
    const nameB = this.sessionRoles.b.name;
    const nameC = this.sessionRoles.c?.name || '第三辩手';
    for (const el of this.chatLog.children) {
      if (el.classList.contains('mr')) {
        const key = el.dataset.key;
        const text = key ? (this.bufs[key] || '') : '';
        if (!text) continue;
        const name = key.startsWith('zhuge-') ? nameA : (key.startsWith('sima-') ? nameB : nameC);
        lines.push(`## ${name}`, '', text, '');
      } else if (el.classList.contains('moderator-strip')) {
        lines.push(`> ${el.textContent.trim()}`, '');
      }
    }
    return lines.join('\n');
  }

  async copyAll() {
    const md = this._buildCurrentMd();
    const btn = this.$('btn-copy-all');
    if (!md.trim()) {
      if (btn) { btn.textContent = '⚠️'; setTimeout(() => btn.textContent = '📋', 1200); }
      return;
    }
    try {
      await navigator.clipboard.writeText(md);
      if (btn) btn.textContent = '✅';
    } catch {
      if (btn) btn.textContent = '❌';
    }
    setTimeout(() => { if (btn) btn.textContent = '📋'; }, 1200);
  }

  exportNow() {
    const md = this._buildCurrentMd();
    if (!md.trim()) { alert('Nothing to export yet. / 暂无内容可导出。'); return; }
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const ts = new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-');
    a.href = url;
    a.download = `yuanzhuo-snapshot-${ts}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  updateTopicPlaceholder() {
    this._initDom();
    const TOPIC_PLACEHOLDERS = {
      free: '例：5 副业先做哪个？  |  今天 BTC 行情怎么看？  |  要不要换工作？',
      selection: '例：无线耳机选哪款？  |  瑜伽垫值得买吗？  |  这个 product listing 能做吗？',
      sidehustle: '例：独立站 vs 电商，先做哪个？  |  内容矩阵怎么起步？',
      negotiation: '例：年度合作续约怎么谈？  |  项目范围变更怎么说？',
      swot: '例：这个产品值得入场吗？  |  我的副业方向 SWOT 分析',
    };
    const val = this.$('template-select').value;
    this.ta.placeholder = TOPIC_PLACEHOLDERS[val] || TOPIC_PLACEHOLDERS.free;
  }

  async newTopic() {
    this._initDom();
    if (this.es) { this.es.close(); this.es = null; }
    this.chatLog.innerHTML = '';
    this.chatLog.appendChild(this.statusBar);
    this.bufs = {}; this.timers = {}; this.curKey = null;
    this.secBuf = ''; this.secEl = null; this.secPanel = null;
    this.researchBuf = ''; this.researchEl = null; this.researchPanel = null;
    this.pendingAttach = []; this.renderAttachPreview();
    this.setStatus('', '');
    this.$('dot-zhuge').classList.remove('active');
    this.$('dot-sima').classList.remove('active');
    const dotThird = this.$('dot-third');
    if (dotThird) dotThird.classList.remove('active');
    this.isPaused = false;
    this.pausedEvents = [];
    this.ta.value = ''; this.ta.style.height = 'auto';
    this.unlock();
    closeHistView();
  }

  resumeTopic(entry, idx) {
    this._initDom();
    closeHistView();
    this.newTopic();
    const topic = (entry && entry.topic) ? entry.topic : (typeof entry === 'string' ? entry : '');
    this.pendingResumeIdx = idx;
    this.ta.value = topic;
    this.ta.style.height = 'auto';
    this.ta.style.height = Math.min(this.ta.scrollHeight, 110) + 'px';
    const sep = document.createElement('div');
    sep.className = 'sep';
    sep.innerHTML = `<span>🔄 续辩模式：${topic.slice(0, 30)}</span>`;
    this.chatLog.appendChild(sep);
    // Render previous history so user can see where the debate left off
    if (entry && entry.history && entry.history.length) {
      let round = 0;
      entry.history.forEach(([role, text]) => {
        if (role === 'zhuge' || role === 'sima' || role === 'third') {
          round++;
          const row = this.mkRow(role, round);
          const b = row.querySelector('[data-content]');
          b.classList.remove('thinking');
          b.innerHTML = marked.parse(text);
          this.chatLog.appendChild(row);
        } else if (role === 'user') {
          const userRow = document.createElement('div');
          userRow.className = 'moderator-strip';
          userRow.textContent = `👤 USER：${text}`;
          this.chatLog.appendChild(userRow);
        }
      });
      this.chatLog.scrollTop = this.chatLog.scrollHeight;
    }
    this.ta.focus();
  }
}

// ── Main panel HTML template ────────────────────────────────────────────────
// Suffix placeholder __SUFFIX__ is replaced at injection time (e.g. with '_L', '_R', or '').
// Instance variable placeholder __PVAR__ is replaced with the panel's JS variable name (e.g. 'p', 'pL', 'pR').
const MAIN_PANEL_HTML = `
<div class="main">
  <header class="hd">
    <div class="fp cl" id="header-a__SUFFIX__">
      <div class="fn"><span id="icon-a__SUFFIX__">🧠</span><span class="header-role-name" id="name-a__SUFFIX__">Analyst / 分析型辩手</span><select class="header-role-select" id="header-role-a-select__SUFFIX__" title="Choose debater A / 选择 A 辩手" onchange="__PVAR__.chooseHeaderRole('a', this.value)"></select><button class="btn-role-settings" title="Configure A model and prompt / 设置 A 角色模型和 prompt" onclick="openPanelRoleSettings(__PVAR__, 'a')">⚙</button><div class="dot c" id="dot-zhuge__SUFFIX__"></div></div>
      <div class="ft" id="role-a-tag__SUFFIX__">Analytical / 分析型</div>
    </div>
    <div class="fp bx" id="header-b__SUFFIX__">
      <div class="fn"><span id="icon-b__SUFFIX__">⚡</span><span class="header-role-name" id="name-b__SUFFIX__">Executor / 执行型辩手</span><select class="header-role-select" id="header-role-b-select__SUFFIX__" title="Choose debater B / 选择 B 辩手" onchange="__PVAR__.chooseHeaderRole('b', this.value)"></select><button class="btn-role-settings" title="Configure B model and prompt / 设置 B 角色模型和 prompt" onclick="openPanelRoleSettings(__PVAR__, 'b')">⚙</button><div class="dot x" id="dot-sima__SUFFIX__"></div></div>
      <div class="ft" id="role-b-tag__SUFFIX__">Execution / 执行型</div>
    </div>
    <div class="fp cx" id="header-c__SUFFIX__">
      <div class="fn"><span id="icon-c__SUFFIX__">🔎</span><span class="header-role-name" id="name-c__SUFFIX__">No third debater / 无第三辩手</span><select class="header-role-select" id="header-role-c-select__SUFFIX__" title="Choose debater C / 选择 C 辩手" onchange="__PVAR__.chooseHeaderRole('c', this.value)"></select><button class="btn-role-settings" title="Configure C model and prompt / 设置 C 角色模型和 prompt" onclick="openPanelRoleSettings(__PVAR__, 'c')">⚙</button><div class="dot m" id="dot-third__SUFFIX__"></div></div>
      <div class="ft" id="role-c-tag__SUFFIX__">Disabled / 未启用</div>
    </div>
  </header>

  <!-- Chat log -->
  <div class="chat" id="chat-log__SUFFIX__">
    <div class="pill" id="status-bar__SUFFIX__"></div>
  </div>

  <!-- Bottom input -->
  <div class="bot">
    <div class="bot-top">
      <div class="imeta">
          <span class="c" id="meta-a__SUFFIX__">🧠 Analyst / 分析型</span>
        <span style="padding:0 6px">Enter topic, press Enter to start / 输入议题，Enter 开始</span>
          <span class="x" id="meta-b__SUFFIX__">Executor / 执行型 ⚡</span>
      </div>
      <div class="bot-controls">
        <div class="role-wrap">
          <span class="role-label">A:</span>
          <select class="role-select" id="role-a-select__SUFFIX__" title="A 角色"></select>
        </div>
        <div class="role-wrap">
          <span class="role-label">B:</span>
          <select class="role-select" id="role-b-select__SUFFIX__" title="B 角色"></select>
        </div>
        <div class="role-wrap">
          <span class="role-label">C:</span>
          <select class="role-select" id="role-c-select__SUFFIX__" title="第三辩手（可选）">
            <option value="">No third debater / 无第三辩手</option>
          </select>
        </div>
        <select class="template-select" id="template-select__SUFFIX__" title="议题模板" onchange="__PVAR__.updateTopicPlaceholder()">
          <option value="free">📋 Free Topic / 自由议题</option>
          <option value="selection">🛍 Ecommerce Review / 电商机会评估</option>
          <option value="sidehustle">💼 Side Project / 副业方向</option>
          <option value="negotiation">🤝 Negotiation Prep / 谈判准备</option>
          <option value="swot">📊 SWOT Decision / SWOT 决策</option>
        </select>
        <button class="btn-mini" title="添加自定义角色和模型" onclick="showCustomRoleModal(__PVAR__)">🧩</button>
        <label class="research-toggle" title="开辩前由调研员搜集背景信息">
          <input type="checkbox" id="research-toggle__SUFFIX__" checked>
          📚 Preheat / 预热
        </label>
        <select class="reasoning-select" id="role-secretary-select__SUFFIX__" title="秘书总结使用的 Agent">
          <option value="">📝 Default Secretary / 默认秘书</option>
        </select>
        <button class="btn-mini" id="btn-copy-all__SUFFIX__" title="复制整段对话" onclick="__PVAR__.copyAll()">📋</button>
        <button class="btn-mini" id="btn-export-now__SUFFIX__" title="Export snapshot as Markdown / 导出当前 Markdown 快照" onclick="__PVAR__.exportNow()">💾</button>
      </div>
    </div>
    <div class="attach-preview" id="attach-preview__SUFFIX__"></div>
    <div class="irow">
      <textarea id="topic-input__SUFFIX__" placeholder="Example: Should we pursue this ecommerce opportunity? / 例：这个电商机会值得做吗？"></textarea>
      <button class="btn-attach" title="上传文件/图片/PDF" onclick="__PVAR__.triggerFileInput()">📎</button>
      <button class="btn-extract" title="从资料抽议题" onclick="showExtractModal()">📥</button>
      <button class="btn btn-pause" id="btn-pause__SUFFIX__" onclick="__PVAR__.togglePause()" disabled>Pause / 暂停</button>
      <button class="btn" id="btn-start__SUFFIX__" onclick="__PVAR__.startRound()">Start / 开始</button>
    </div>
    <input type="file" id="file-input__SUFFIX__" multiple accept="image/*,.pdf,.md,.txt,.py,.json,.yaml,.yml,.csv,.html,.js,.ts,.sh,.log,.sql,.tsx,.jsx,.css,.xml,.toml,.ini" style="display:none" onchange="__PVAR__.handleFiles(this.files)">
  </div>
</div>
`;
