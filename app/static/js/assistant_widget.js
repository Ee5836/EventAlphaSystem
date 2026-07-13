/**
 * BubbleEvent — AI Research Assistant
 * Homepage: full inline chat.  Other pages: draggable floating window.
 * Both share the same session — the widget is the "mobile version" of the homepage agent.
 * Vanilla JS, zero dependencies.
 */
(function() {
    'use strict';

    // ═══════════════════════ STATE ═══════════════════════════════════
    function _checkIsHomepage() { return !!document.getElementById('homeChatBody'); }
    let currentSessionId = localStorage.getItem('ai_session_id') || '';
    let isProcessing = false;
    let isOpen = false;
    let isFullscreen = false;
    let _homeGeneration = 0;  // bumped on each homepage re-entry, invalidates stale DOM ops

    function _saveState() {
        localStorage.setItem('ai_is_open', isOpen ? 'true' : 'false');
        localStorage.setItem('ai_session_id', currentSessionId || '');
    }

    // ═══════════════════════ SESSION ════════════════════════════════

    async function autoCreateSession() {
        try {
            const resp = await fetch('/api/v1/assistant/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: '新对话' })
            });
            const data = await resp.json();
            if (data.success) { currentSessionId = data.data.id; _saveState(); }
        } catch(e) { console.warn('Auto-create session failed:', e); }
    }

    async function _fetchSession(sid) {
        try {
            const resp = await fetch('/api/v1/assistant/sessions/' + sid);
            const data = await resp.json();
            if (data.success) return data.data;
        } catch(e) {}
        return null;
    }

    // ═══════════════════════ SHARED MESSAGE SENDING ══════════════════

    function _chatBody() {
        return _checkIsHomepage() ? document.getElementById('homeChatBody')
                                  : document.getElementById('aiChatBody');
    }

    function _chatLoadingId() {
        return _checkIsHomepage() ? 'homeAiLoading' : 'aiLoadingMsg';
    }

    function _appendToChat(role, content, messageId) {
        var body = _chatBody();
        if (!body) return;
        if (_checkIsHomepage()) {
            // Remove empty state on homepage
            var empty = document.getElementById('homeChatEmpty');
            if (empty) empty.remove();
        } else {
            var empty2 = body.querySelector('.ai-empty-state');
            if (empty2) empty2.remove();
        }
        var div = document.createElement('div');
        div.className = 'ai-msg ' + role;
        if (messageId) div.setAttribute('data-msg-id', messageId);
        div.innerHTML = '<div class="ai-msg-bubble"><div class="ai-msg-content">' + _simpleMD(content) + '</div><button class="ai-msg-del" title="删除">&times;</button></div>';
        body.appendChild(div);
        body.scrollTop = body.scrollHeight;
    }

    function _appendLoading() {
        var body = _chatBody();
        if (!body) return;
        var div = document.createElement('div');
        div.className = 'ai-msg assistant';
        div.id = _chatLoadingId();
        div.innerHTML = '<div class="ai-msg-bubble"><div class="ai-typing"><span></span><span></span><span></span></div></div>';
        body.appendChild(div);
        body.scrollTop = body.scrollHeight;
    }

    function _removeLoading() {
        var el = document.getElementById(_chatLoadingId());
        if (el) el.remove();
    }

    async function _sendMessage(question) {
        if (!currentSessionId) return;
        if (isProcessing) return;
        isProcessing = true;

        var genAtStart = _homeGeneration;  // snapshot for stale-check
        _appendToChat('user', question);
        _appendLoading();

        try {
            const resp = await fetch('/api/v1/assistant/sessions/' + currentSessionId + '/messages', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: question, focus_industries: getFocusIndustries() }),
            });
            const data = await resp.json();

            // If homepage DOM was replaced while we were waiting, skip direct
            // DOM manipulation to avoid racing with renderHomepageResponse().
            // Instead, trigger a fresh restore from DB which is race-free.
            if (_homeGeneration !== genAtStart) {
                isProcessing = false;
                if (_checkIsHomepage()) {
                    renderHomepageResponse();
                }
                return;
            }

            _removeLoading();
            _appendToChat('assistant', data.success ? data.data.response : ('请求失败: ' + (data.error || '未知错误')));
        } catch(e) {
            if (_homeGeneration !== genAtStart) {
                isProcessing = false;
                if (_checkIsHomepage()) {
                    renderHomepageResponse();
                }
                return;
            }
            _removeLoading();
            _appendToChat('assistant', '网络请求失败，请稍后重试。');
        }

        isProcessing = false;
        _saveState();
    }

    // ═══════════════════════ HOMEPAGE CHAT ═══════════════════════════

    function _enterChatMode() {
        // Works on both SSR and SPA homepage
        var layout = document.getElementById('homePageLayout');
        if (layout) layout.classList.add('chatting');
    }

    function _hideChips() {
        var chips = document.getElementById('homeSuggestions');
        if (chips) {
            chips.style.transition = 'opacity 0.35s ease, transform 0.35s ease, max-height 0.35s ease';
            chips.style.opacity = '0'; chips.style.transform = 'translateY(8px)';
            chips.style.pointerEvents = 'none'; chips.style.maxHeight = '0'; chips.style.marginBottom = '0';
        }
    }

    async function _sendFromHomepage(question) {
        _enterChatMode(); _hideChips();
        var hint = document.querySelector('.home-input-hint');
        if (hint) hint.style.opacity = '0';
        await _sendMessage(question);
    }

    async function renderHomepageResponse() {
        if (!currentSessionId) return;
        try {
            var session = await _fetchSession(currentSessionId);
            if (!session || !session.messages || !session.messages.length) return;
            var chatBody = document.getElementById('homeChatBody');
            if (!chatBody) return;
            _enterChatMode(); _hideChips();
            var emptyEl = document.getElementById('homeChatEmpty');
            if (emptyEl) emptyEl.remove();

            // ── Differential update (race-free) ─────────────────────
            // Build map of currently rendered message IDs
            var rendered = {};  // id → DOM element
            chatBody.querySelectorAll('.ai-msg[data-msg-id]').forEach(function(el) {
                rendered[el.getAttribute('data-msg-id')] = el;
            });

            // Build set of session message IDs
            var sessionIds = {};
            session.messages.forEach(function(m) { sessionIds[m.id] = true; });

            // Remove DOM elements for deleted messages
            for (var rid in rendered) {
                if (!sessionIds[rid]) {
                    rendered[rid].remove();
                }
            }

            // Append new messages (preserving chronological order)
            session.messages.forEach(function(m) {
                if (!rendered[m.id]) {
                    _appendToChat(m.role, m.content, m.id);
                }
            });

            chatBody.classList.add('content-received');
            setTimeout(function() { chatBody.classList.remove('content-received'); }, 400);
        } catch(e) { console.warn('Load history failed:', e); }
    }

    // ═══════════════════════ WIDGET: DOM ═════════════════════════════

    function getDefaultPos() {
        var saved = localStorage.getItem('ai_window_pos');
        if (saved) { try { var p = JSON.parse(saved); if (p.left && p.top) return p; } catch(e) {} }
        return { left: Math.max(100, window.innerWidth - 540), top: Math.max(60, (window.innerHeight - 580) / 2) };
    }
    function getDefaultSize() {
        var saved = localStorage.getItem('ai_window_size');
        if (saved) { try { var s = JSON.parse(saved); if (s.w && s.h) return s; } catch(e) {} }
        return { w: 480, h: 580 };
    }

    function buildWidget() {
        var pos = getDefaultPos(), sz = getDefaultSize();
        var winCls = isOpen ? 'ai-window' : 'ai-window closed';
        var fabCls = isOpen ? 'ai-fab hidden' : 'ai-fab';
        var c = document.createElement('div');
        c.id = 'aiWidget';
        c.style.cssText = 'position:fixed;top:0;left:0;overflow:visible;pointer-events:none;z-index:9990;';
        c.innerHTML =
            '<button class="' + fabCls + '" id="aiFab" title="AI 研究助手"><i class="bi bi-robot"></i><span class="ai-fab-pulse"></span></button>' +
            '<div class="' + winCls + '" id="aiWindow" style="left:' + pos.left + 'px;top:' + pos.top + 'px;width:' + sz.w + 'px;height:' + sz.h + 'px;">' +
            '<div class="ai-window-header" id="aiWindowHeader">' +
            '<span class="ai-win-dot"></span><span class="ai-win-title">AI 研究助手</span>' +
            '<span class="ai-win-label" id="aiSessionLabel">就绪</span>' +
            '<div class="ai-win-actions">' +
            '<button class="ai-win-btn" id="aiBtnNew" title="新对话"><i class="bi bi-plus-lg"></i></button>' +
            '<button class="ai-win-btn" id="aiBtnSettings" title="行业设置"><i class="bi bi-gear-fill"></i></button>' +
            '<button class="ai-win-btn" id="aiBtnHistory" title="历史对话"><i class="bi bi-list"></i></button>' +
            '<button class="ai-win-btn ai-win-fullscreen" id="aiBtnFullscreen" title="全屏"><i class="bi bi-arrows-fullscreen"></i></button>' +
            '<button class="ai-win-btn ai-win-close" id="aiBtnClose" title="关闭"><i class="bi bi-x-lg"></i></button>' +
            '</div></div>' +
            '<div class="ai-session-list" id="aiSessionList" style="display:none"><div class="ai-session-items" id="aiSessionItems">' +
            '<div class="text-center text-muted py-2" style="font-size:0.7rem">加载中...</div></div></div>' +
            '<div class="ai-settings-panel" id="aiSettingsPanel" style="display:none">' +
            '<div class="ai-settings-header">关注行业设置</div>' +
            '<div class="ai-settings-body" id="aiSettingsBody">加载中...</div>' +
            '<div class="ai-settings-footer">' +
            '<button class="ai-settings-save-btn" id="aiSettingsSave">保存</button>' +
            '<button class="ai-settings-cancel-btn" id="aiSettingsCancel">取消</button>' +
            '</div></div>' +
            '<div class="ai-window-body" id="aiChatBody">' + buildEmptyState() + '</div>' +
            '<div class="ai-window-footer"><div class="ai-input-row">' +
            '<input class="ai-input" id="aiChatInput" type="text" placeholder="输入问题，Enter 发送..." autocomplete="off">' +
            '<button class="ai-send-btn" id="aiSendBtn" title="发送"><i class="bi bi-send-fill"></i></button>' +
            '</div></div>' +
            '<div class="ai-resize-handle" id="aiResizeHandle"></div></div>';
        document.body.appendChild(c);
    }

    function buildEmptyState() {
        return '<div class="ai-empty-state"><div class="ai-empty-icon">◆</div>' +
            '<div class="ai-empty-title">BubbleEvent 研究助手</div>' +
            '<div class="ai-empty-desc">全局事件分析 · 时间线统计 · 简报查询 · 走势解读 · 系统状态</div>' +
            '<div class="ai-focus-tags" id="aiFocusTags" style="display:none"></div>' +
            '<div class="ai-quick-asks">' +
            '<button class="ai-quick-btn" onclick="window._aiWidget.quickAsk(\'今天有什么大事\')">📰 今天有什么大事</button>' +
            '<button class="ai-quick-btn" onclick="window._aiWidget.quickAsk(\'分析当前时间线状态\')">🕸️ 分析时间线</button>' +
            '<button class="ai-quick-btn" onclick="window._aiWidget.quickAsk(\'帮我更新简报\')">📋 更新简报</button>' +
            '<button class="ai-quick-btn" onclick="window._aiWidget.quickAsk(\'系统当前状态怎么样\')">📊 系统状态</button>' +
            '</div></div>';
    }

    // ═══════════════════════ WIDGET: EVENTS ══════════════════════════

    function bindWidgetEvents() {
        document.getElementById('aiFab').addEventListener('click', toggle);
        document.getElementById('aiBtnClose').addEventListener('click', close);
        document.getElementById('aiBtnFullscreen').addEventListener('click', toggleFullscreen);
        document.getElementById('aiBtnNew').addEventListener('click', newSession);
        document.getElementById('aiBtnSettings').addEventListener('click', toggleSettings);
        document.getElementById('aiBtnHistory').addEventListener('click', toggleSessionList);

        // Settings panel save/cancel
        var saveBtn = document.getElementById('aiSettingsSave');
        var cancelBtn = document.getElementById('aiSettingsCancel');
        if (saveBtn) saveBtn.addEventListener('click', saveSettings);
        if (cancelBtn) cancelBtn.addEventListener('click', function() {
            document.getElementById('aiSettingsPanel').style.display = 'none';
        });

        var input = document.getElementById('aiChatInput');
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); widgetSend(); }
        });
        document.getElementById('aiSendBtn').addEventListener('click', widgetSend);

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && isOpen && !isFullscreen) close();
        });

        // Drag
        var header = document.getElementById('aiWindowHeader');
        var win = document.getElementById('aiWindow');
        var dragState = null;
        header.addEventListener('mousedown', function(e) {
            if (e.target.closest('button') || isFullscreen) return;
            var r = win.getBoundingClientRect();
            dragState = { sx: e.clientX, sy: e.clientY, ol: r.left, ot: r.top };
            win.classList.add('dragging'); e.preventDefault();
        });
        document.addEventListener('mousemove', function(e) {
            if (!dragState) return;
            var nl = Math.max(-win.getBoundingClientRect().width + 80, Math.min(window.innerWidth - 80, dragState.ol + e.clientX - dragState.sx));
            var nt = Math.max(-40, Math.min(window.innerHeight - 40, dragState.ot + e.clientY - dragState.sy));
            win.style.left = nl + 'px'; win.style.top = nt + 'px';
        });
        document.addEventListener('mouseup', function() {
            if (dragState) {
                win.classList.remove('dragging');
                var r = win.getBoundingClientRect();
                localStorage.setItem('ai_window_pos', JSON.stringify({ left: r.left, top: r.top }));
                dragState = null;
            }
        });

        // Resize
        var handle = document.getElementById('aiResizeHandle');
        var resizeState = null;
        handle.addEventListener('mousedown', function(e) {
            if (isFullscreen) return;
            var r = win.getBoundingClientRect();
            resizeState = { sx: e.clientX, sy: e.clientY, ow: r.width, oh: r.height };
            e.preventDefault(); e.stopPropagation();
        });
        document.addEventListener('mousemove', function(e) {
            if (!resizeState) return;
            win.style.width = Math.max(380, Math.min(window.innerWidth * 0.9, resizeState.ow + e.clientX - resizeState.sx)) + 'px';
            win.style.height = Math.max(420, Math.min(window.innerHeight * 0.9, resizeState.oh + e.clientY - resizeState.sy)) + 'px';
        });
        document.addEventListener('mouseup', function() {
            if (resizeState) {
                var r = win.getBoundingClientRect();
                localStorage.setItem('ai_window_size', JSON.stringify({ w: r.width, h: r.height }));
                resizeState = null;
            }
        });

        // Click outside session list
        document.addEventListener('click', function(e) {
            var list = document.getElementById('aiSessionList');
            if (list && list.style.display !== 'none' && !e.target.closest('#aiSessionList') && !e.target.closest('#aiBtnHistory')) {
                list.style.display = 'none';
            }
        });

        // Message delete button delegation — widget body
        var chatBody = document.getElementById('aiChatBody');
        if (chatBody) {
            chatBody.addEventListener('click', function(e) {
                var delBtn = e.target.closest('.ai-msg-del');
                if (!delBtn) return;
                var msgDiv = delBtn.closest('.ai-msg');
                if (!msgDiv) return;
                var msgId = msgDiv.getAttribute('data-msg-id');
                if (!msgId) return;
                deleteMessage(msgId, msgDiv);
            });
        }
    }

    // ═══════════════════════ FOCUS INDUSTRIES ═══════════════════════

    function getFocusIndustries() {
        try {
            var raw = localStorage.getItem('bubbleevent_focus_industries');
            return raw ? JSON.parse(raw) : [];
        } catch(e) { return []; }
    }

    function saveFocusIndustries(industries) {
        localStorage.setItem('bubbleevent_focus_industries', JSON.stringify(industries));
        renderFocusTags();
    }

    function renderFocusTags() {
        var industries = getFocusIndustries();
        var container = document.getElementById('aiFocusTags');
        if (!container) return;
        if (!industries.length) {
            container.style.display = 'none';
            return;
        }
        container.style.display = 'flex';
        container.innerHTML = '<span class="ai-focus-label">关注:</span>' +
            industries.slice(0, 5).map(function(ind) {
                return '<span class="ai-focus-tag">' + escHtml(ind) + '</span>';
            }).join('') +
            (industries.length > 5 ? '<span class="ai-focus-tag">+' + (industries.length - 5) + '</span>' : '');
    }

    function toggleSettings() {
        var panel = document.getElementById('aiSettingsPanel');
        var list = document.getElementById('aiSessionList');
        if (!panel) return;
        if (panel.style.display === 'none') {
            if (list) list.style.display = 'none';
            panel.style.display = 'block';
            loadSettingsPanel();
        } else {
            panel.style.display = 'none';
        }
    }

    async function loadSettingsPanel() {
        var body = document.getElementById('aiSettingsBody');
        if (!body) return;
        body.innerHTML = '<div class="text-center text-muted py-2" style="font-size:0.7rem">加载行业列表...</div>';

        try {
            var resp = await fetch('/api/v1/assistant/industries');
            var data = await resp.json();
            if (!data.success) { body.innerHTML = '<div class="text-danger" style="font-size:0.7rem;padding:8px">加载失败</div>'; return; }

            var selected = getFocusIndustries();
            var cats = data.data.categories;
            var html = '';

            for (var catName in cats) {
                var industries = cats[catName];
                if (!industries.length) continue;
                html += '<div class="ai-settings-cat">' + escHtml(catName) + '</div>';
                html += '<div class="ai-settings-tags">';
                industries.forEach(function(ind) {
                    var isChecked = selected.indexOf(ind) >= 0;
                    html += '<label class="ai-settings-tag' + (isChecked ? ' checked' : '') + '">' +
                        '<input type="checkbox" value="' + escHtml(ind) + '"' + (isChecked ? ' checked' : '') + '>' +
                        escHtml(ind) + '</label>';
                });
                html += '</div>';
            }
            body.innerHTML = html;
        } catch(e) {
            body.innerHTML = '<div class="text-danger" style="font-size:0.7rem;padding:8px">加载失败: ' + e.message + '</div>';
        }
    }

    function saveSettings() {
        var checkboxes = document.querySelectorAll('#aiSettingsBody input[type="checkbox"]:checked');
        var selected = [];
        checkboxes.forEach(function(cb) { selected.push(cb.value); });
        saveFocusIndustries(selected);
        document.getElementById('aiSettingsPanel').style.display = 'none';
        toast('关注行业已保存 (' + selected.length + '个)');
    }

    // ═══════════════════════ WIDGET: API ═════════════════════════════

    function open(opts) {
        opts = opts || {};
        var win = document.getElementById('aiWindow'), fab = document.getElementById('aiFab');
        if (!win || !fab) return;
        if (!win.style.left || win.style.left === '0px') {
            var p = getDefaultPos(), s = getDefaultSize();
            win.style.left = p.left + 'px'; win.style.top = p.top + 'px';
            win.style.width = s.w + 'px'; win.style.height = s.h + 'px';
        }
        win.classList.remove('closed'); fab.classList.add('hidden');
        isOpen = true; _saveState();
        if (!opts.silent) document.getElementById('aiChatInput').focus();
    }

    function close() {
        var win = document.getElementById('aiWindow'), fab = document.getElementById('aiFab');
        if (!win || !fab) return;
        if (isFullscreen) toggleFullscreen();
        win.classList.add('closed'); fab.classList.remove('hidden');
        isOpen = false; _saveState();
    }

    function toggle() { isOpen ? close() : open(); }

    function toggleFullscreen() {
        var win = document.getElementById('aiWindow');
        if (!win) return;
        if (isFullscreen) {
            win.classList.remove('fullscreen');
            var p = getDefaultPos(), s = getDefaultSize();
            win.style.left = p.left + 'px'; win.style.top = p.top + 'px';
            win.style.width = s.w + 'px'; win.style.height = s.h + 'px';
            document.getElementById('aiBtnFullscreen').innerHTML = '<i class="bi bi-arrows-fullscreen"></i>';
            isFullscreen = false;
        } else {
            var r = win.getBoundingClientRect();
            localStorage.setItem('ai_window_pos', JSON.stringify({ left: r.left, top: r.top }));
            localStorage.setItem('ai_window_size', JSON.stringify({ w: r.width, h: r.height }));
            win.classList.add('fullscreen');
            document.getElementById('aiBtnFullscreen').innerHTML = '<i class="bi bi-fullscreen-exit"></i>';
            isFullscreen = true;
        }
    }

    async function widgetSend() {
        var input = document.getElementById('aiChatInput');
        var q = (input.value || '').trim();
        if (!q) return;
        if (!currentSessionId) { await autoCreateSession(); if (!currentSessionId) return; }
        input.value = '';
        var btn = document.getElementById('aiSendBtn');
        if (btn) btn.disabled = true;
        try {
            await _sendMessage(q);
        } finally {
            if (btn) btn.disabled = false;
        }
        input.focus();
    }

    // ═══════════════════════ WIDGET: SESSIONS ════════════════════════

    async function restoreWidgetSession(sid) {
        try {
            var session = await _fetchSession(sid);
            if (session) {
                currentSessionId = sid; _saveState();
                document.getElementById('aiSessionLabel').textContent = (session.title || '会话').slice(0, 12);
                if (session.messages && session.messages.length) _renderWidgetMessages(session.messages);
                return;
            }
        } catch(e) {}
        currentSessionId = ''; _saveState(); await autoCreateSession();
    }

    function _renderWidgetMessages(messages) {
        var body = _chatBody();
        if (!body) return;
        body.innerHTML = '';
        if (!messages.length) { body.innerHTML = buildEmptyState(); renderFocusTags(); return; }
        messages.forEach(function(m) { _appendToChat(m.role, m.content, m.id); });
        body.scrollTop = body.scrollHeight;
    }

    function toggleSessionList() {
        var list = document.getElementById('aiSessionList');
        if (!list) return;
        if (list.style.display === 'none') { refreshSessions(); list.style.display = 'block'; }
        else list.style.display = 'none';
    }

    async function refreshSessions() {
        var items = document.getElementById('aiSessionItems');
        if (!items) return;
        try {
            var resp = await fetch('/api/v1/assistant/sessions?limit=50');
            var data = await resp.json();
            if (!data.success || !data.data.length) {
                items.innerHTML = '<div class="text-center text-muted py-2" style="font-size:0.7rem">暂无历史对话</div>';
                return;
            }
            items.innerHTML = data.data.map(function(s) {
                return '<div class="ai-session-row' + (s.id === currentSessionId ? ' active' : '') + '" onclick="window._aiWidget.loadSession(\'' + s.id + '\')">' +
                    '<span class="ai-session-name">' + escHtml(s.title || '未命名') + '</span>' +
                    '<button class="ai-session-del" onclick="event.stopPropagation();window._aiWidget.deleteSession(\'' + s.id + '\',event)" title="删除">x</button></div>';
            }).join('');
        } catch(e) { items.innerHTML = '<div class="text-center text-danger py-2" style="font-size:0.7rem">加载失败</div>'; }
    }

    async function loadSession(sid) {
        currentSessionId = sid; _saveState();
        document.getElementById('aiSessionList').style.display = 'none';
        document.getElementById('aiSessionLabel').textContent = '会话 ' + sid.slice(-6);
        await restoreWidgetSession(sid);
    }

    async function deleteSession(sid, e) {
        e.stopPropagation();
        if (!confirm('确定删除此对话？')) return;
        var btn = e.target.closest('button');
        if (btn) { btn.disabled = true; btn.style.opacity = '0.4'; }
        try {
            await fetch('/api/v1/assistant/sessions/' + sid, { method: 'DELETE' });
            if (sid === currentSessionId) {
                currentSessionId = '';
                var homeBody = document.getElementById('homeChatBody');
                var widgetBody = document.getElementById('aiChatBody');
                if (homeBody) { homeBody.innerHTML = '<div class="home-chat-empty" id="homeChatEmpty"><div class="home-chat-empty-icon">◆</div><h2 class="home-chat-empty-title">有什么可以帮你？</h2><p class="home-chat-empty-desc">基于实时事件与行情数据的 AI 智能分析</p></div>'; renderFocusTags(); }
                if (widgetBody) { widgetBody.innerHTML = buildEmptyState(); renderFocusTags(); }
                await autoCreateSession();
            }
            refreshSessions();
            refreshHomeSessions();
        } catch(ex) { toast('删除失败'); }
        finally { if (btn) { btn.disabled = false; btn.style.opacity = ''; } }
    }

    async function deleteMessage(msgId, msgDiv) {
        if (!currentSessionId) return;
        try {
            var resp = await fetch(
                '/api/v1/assistant/sessions/' + currentSessionId + '/messages/' + msgId,
                { method: 'DELETE' }
            );
            var data = await resp.json();
            if (data.success) {
                var deletedIds = data.data.deleted_ids || [];
                var body = _chatBody();
                if (body) {
                    msgDiv.remove();
                    deletedIds.forEach(function(did) {
                        if (did === msgId) return;
                        var pairEl = body.querySelector('.ai-msg[data-msg-id="' + did + '"]');
                        if (pairEl) pairEl.remove();
                    });
                    if (!body.querySelector('.ai-msg')) {
                        body.innerHTML = buildEmptyState();
                        renderFocusTags();
                    }
                }
            } else {
                toast('删除失败');
            }
        } catch(ex) { toast('删除失败'); }
    }

    function _exitChatMode() {
        if (window._exitHomeChatMode) window._exitHomeChatMode();
    }

    // ═══════════════════════ HOMEPAGE HISTORY PANEL ═══════════════════

    var _homeSessionOffset = 0;
    var _homeSessionTotal = 0;

    function _initHomepageHistory() {
        var histBtn = document.getElementById('homeBtnHistory');
        var newBtn = document.getElementById('homeBtnNewChat');
        var clearBtn = document.getElementById('homeClearAllBtn');
        var loadMoreBtn = document.getElementById('homeLoadMoreBtn');
        var panel = document.getElementById('homeSessionPanel');
        var header = document.getElementById('homeHeader');

        if (!histBtn || !panel) return;

        // Toggle panel
        histBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (panel.style.display === 'none') {
                _homeSessionOffset = 0;
                refreshHomeSessions();
                panel.style.display = 'flex';
            } else {
                panel.style.display = 'none';
            }
        });

        // New chat
        if (newBtn) {
            newBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                newSession();
            });
        }

        // Clear all
        if (clearBtn) {
            clearBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                if (!confirm('确定清空所有对话历史？此操作不可撤销。')) return;
                clearBtn.disabled = true;
                clearBtn.textContent = '清空中...';
                fetch('/api/v1/assistant/sessions', { method: 'DELETE' })
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.success) {
                            currentSessionId = ''; _saveState();
                            var hb = document.getElementById('homeChatBody');
                            if (hb) { hb.innerHTML = '<div class="home-chat-empty" id="homeChatEmpty"><div class="home-chat-empty-icon">◆</div><h2 class="home-chat-empty-title">有什么可以帮你？</h2><p class="home-chat-empty-desc">基于实时事件与行情数据的 AI 智能分析</p></div>'; renderFocusTags(); _exitChatMode(); }
                            var wb = document.getElementById('aiChatBody');
                            if (wb) { wb.innerHTML = buildEmptyState(); renderFocusTags(); }
                            autoCreateSession();
                            panel.style.display = 'none';
                            toast('已清空所有对话 (' + data.data.deleted_count + ' 个)');
                        } else { toast('清空失败'); }
                    })
                    .catch(function() { toast('清空失败'); })
                    .finally(function() { clearBtn.disabled = false; clearBtn.textContent = '清空全部'; });
            });
        }

        // Load more
        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                _homeSessionOffset += 30;
                refreshHomeSessions(true);
            });
        }

        // Click outside to close
        document.addEventListener('click', function(e) {
            if (panel.style.display !== 'none' && !e.target.closest('#homeSessionPanel') && !e.target.closest('#homeBtnHistory')) {
                panel.style.display = 'none';
            }
        });

        // Message delete button delegation — homepage body
        // (extracted from bindWidgetEvents so it works when widget UI is disabled)
        var homeBody = document.getElementById('homeChatBody');
        if (homeBody && !homeBody._deleteDelegationBound) {
            homeBody._deleteDelegationBound = true;
            homeBody.addEventListener('click', function(e) {
                var delBtn = e.target.closest('.ai-msg-del');
                if (!delBtn) return;
                var msgDiv = delBtn.closest('.ai-msg');
                if (!msgDiv) return;
                var msgId = msgDiv.getAttribute('data-msg-id');
                if (!msgId) return;
                deleteMessage(msgId, msgDiv);
            });
        }
    }

    async function refreshHomeSessions(append) {
        var items = document.getElementById('homeSessionItems');
        var footer = document.getElementById('homeSessionFooter');
        if (!items) return;
        if (!append) { _homeSessionOffset = 0; items.innerHTML = '<div class="text-center text-muted py-2" style="font-size:0.7rem">加载中...</div>'; }

        try {
            var resp = await fetch('/api/v1/assistant/sessions?offset=' + _homeSessionOffset + '&limit=30');
            var data = await resp.json();
            if (!data.success || !data.data.length) {
                if (!append) items.innerHTML = '<div class="text-center text-muted py-2" style="font-size:0.7rem">暂无历史对话</div>';
                if (footer) footer.style.display = 'none';
                return;
            }
            _homeSessionTotal = data.total;

            var html = (append ? items.innerHTML : '');
            data.data.forEach(function(s) {
                var title = escHtml((s.title || '未命名').slice(0, 35));
                var time = s.updated_at ? s.updated_at.slice(5, 16).replace('T', ' ') : '';
                var activeClass = s.id === currentSessionId ? ' active' : '';
                html += '<div class="home-session-row' + activeClass + '" data-sid="' + s.id + '">' +
                    '<span class="home-session-row-time">' + escHtml(time) + '</span>' +
                    '<span class="home-session-row-name">' + title + '</span>' +
                    '<button class="home-session-row-del" title="删除">×</button></div>';
            });
            items.innerHTML = html;

            // Has more?
            var loaded = _homeSessionOffset + data.data.length;
            if (footer) footer.style.display = (loaded < _homeSessionTotal) ? 'block' : 'none';

            // Bind row clicks (load session) and delete buttons
            items.querySelectorAll('.home-session-row').forEach(function(row) {
                row.addEventListener('click', function(e) {
                    if (e.target.closest('.home-session-row-del')) return;
                    var sid = row.getAttribute('data-sid');
                    if (!sid) return;
                    document.getElementById('homeSessionPanel').style.display = 'none';
                    loadHomeSession(sid);
                });
                var delBtn = row.querySelector('.home-session-row-del');
                if (delBtn) {
                    delBtn.addEventListener('click', function(e) {
                        var sid = row.getAttribute('data-sid');
                        if (sid) deleteSession(sid, e);
                    });
                }
            });
        } catch(e) { items.innerHTML = '<div class="text-center text-danger py-2" style="font-size:0.7rem">加载失败</div>'; }
    }

    async function loadHomeSession(sid) {
        currentSessionId = sid; _saveState();
        try {
            var session = await _fetchSession(sid);
            if (!session) { return; }
            var homeBody = document.getElementById('homeChatBody');
            if (!homeBody) return;
            _enterHomeChatMode();
            var emptyEl = document.getElementById('homeChatEmpty');
            if (emptyEl) emptyEl.remove();
            homeBody.innerHTML = '';
            if (session.messages && session.messages.length) {
                session.messages.forEach(function(m) { _appendToChat(m.role, m.content, m.id); });
            } else {
                homeBody.innerHTML = '<div class="home-chat-empty" id="homeChatEmpty"><div class="home-chat-empty-icon">◆</div><h2 class="home-chat-empty-title">有什么可以帮你？</h2><p class="home-chat-empty-desc">基于实时事件与行情数据的 AI 智能分析</p></div>';
            }
            homeBody.scrollTop = homeBody.scrollHeight;
        } catch(e) { toast('加载会话失败'); }
    }

    function _enterHomeChatMode() {
        if (window._enterHomeChatMode) window._enterHomeChatMode();
    }

    async function newSession() {
        document.getElementById('aiSessionList').style.display = 'none';
        var homePanel = document.getElementById('homeSessionPanel');
        if (homePanel) homePanel.style.display = 'none';
        try {
            var resp = await fetch('/api/v1/assistant/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: '新对话' }) });
            var data = await resp.json();
            if (data.success) {
                currentSessionId = data.data.id; _saveState();
                document.getElementById('aiSessionLabel').textContent = '新对话';
                // Clear homepage chat if present
                var homeBody = document.getElementById('homeChatBody');
                if (homeBody) { homeBody.innerHTML = '<div class="home-chat-empty" id="homeChatEmpty"><div class="home-chat-empty-icon">◆</div><h2 class="home-chat-empty-title">有什么可以帮你？</h2><p class="home-chat-empty-desc">基于实时事件与行情数据的 AI 智能分析</p></div>'; renderFocusTags(); _exitChatMode(); }
                var widgetBody = document.getElementById('aiChatBody');
                if (widgetBody) { widgetBody.innerHTML = buildEmptyState(); renderFocusTags(); }
            }
        } catch(ex) { toast('创建失败'); }
    }

    // ═══════════════════════ PUBLIC quickAsk ══════════════════════════

    function quickAsk(question) {
        if (!question) return;
        if (_checkIsHomepage()) {
            // Homepage: render inline, set flag for future navigation
            localStorage.setItem('ai_from_homepage', 'true');
            if (!currentSessionId) {
                autoCreateSession().then(function() { _sendFromHomepage(question); })
                    .catch(function() { toast('创建会话失败'); });
            } else {
                _sendFromHomepage(question);
            }
            return;
        }
        // Non-homepage: open widget, pre-fill input if possible, send
        open();
        var inp = document.getElementById('aiChatInput');
        if (inp) inp.value = question;
        if (!currentSessionId) {
            autoCreateSession().then(function() { widgetSend(); })
                .catch(function() { toast('创建会话失败'); });
        } else {
            widgetSend();
        }
    }

    // ═══════════════════════ HELPERS ═════════════════════════════════

    function _simpleMD(text) {
        if (!text) return '';
        var html = escHtml(text);
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/(?:^|\n)[-*] (.+?)(?=\n|$)/g, '\n<li>$1</li>');
        if (/<li>/.test(html)) html = '<ul>' + html + '</ul>';
        html = html.replace(/<\/li>\n<li>/g, '</li><li>');
        html = html.replace(/(?:^|\n)(\d+)\. (.+?)(?=\n|$)/g, function(m, num, text) { return '\n<li>' + text + '</li>'; });
        html = html.replace(/\n\n/g, '</p><p>'); html = html.replace(/\n/g, '<br>');
        if (!html.startsWith('<')) html = '<p>' + html + '</p>';
        return html;
    }
    function escHtml(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
    function toast(msg) {
        var ex = document.querySelector('.ai-toast'); if (ex) ex.remove();
        var d = document.createElement('div'); d.className = 'ai-toast';
        d.style.cssText = 'position:fixed;bottom:80px;right:24px;z-index:9999;padding:8px 18px;border-radius:10px;font-size:0.78rem;background:var(--bg-elevated);border:1px solid var(--border-default);color:var(--text-primary);box-shadow:0 8px 32px rgba(0,0,0,0.5);animation:fadeIn 0.3s ease;';
        d.textContent = msg; document.body.appendChild(d);
        setTimeout(function() { if (d.parentNode) d.remove(); }, 2500);
    }

    // ═══════════════════════ SPA NAVIGATION HOOKS ═════════════════════

    function _setupRouterHooks(widgetOn) {
        if (!window._router) return;
        window._router.onBeforeNavigate(function(data) {
            if (data.isLeavingHomepage && currentSessionId) {
                // Mark that we're coming from homepage with a conversation
                localStorage.setItem('ai_from_homepage', 'true');
            }
        });
        window._router.onAfterNavigate(function(data) {
            // Only auto-open floating widget when widget UI is enabled
            if (widgetOn && data.isLeavingHomepage && currentSessionId && !isOpen) {
                // Arrived at non-homepage: auto-open widget with conversation
                localStorage.removeItem('ai_from_homepage');
                open();
                restoreWidgetSession(currentSessionId);
            }
            if (data.isEnteringHomepage && currentSessionId) {
                // Arrived back at homepage: close floating widget, restore inline conversation
                _homeGeneration++;  // invalidate any in-flight DOM ops from previous page
                if (isOpen) close();
                if (widgetOn) {
                    var fab = document.getElementById('aiFab');
                    if (fab) fab.style.display = 'none';
                }
                // Small delay to let fragment DOM settle
                setTimeout(function() {
                    renderHomepageResponse();
                    _initHomepageHistory();
                }, 80);
            }
        });
    }

    // ═══════════════════════ EXPORT ══════════════════════════════════

    window._aiWidget = {
        open: open, close: close, toggle: toggle, quickAsk: quickAsk,
        toggleFullscreen: toggleFullscreen, toggleSessionList: toggleSessionList,
        newSession: newSession, loadSession: loadSession, deleteSession: deleteSession,
        deleteMessage: deleteMessage,
        refreshSessions: refreshSessions, refreshHomeSessions: refreshHomeSessions, send: widgetSend,
        renderHomepageResponse: renderHomepageResponse,
        initHomepageHistory: _initHomepageHistory,
        get isOpen() { return isOpen; },
        get hasSession() { return !!currentSessionId; },
    };

    // ═══════════════════════ INIT ════════════════════════════════════

    function init() {
        if (document.getElementById('aiWidget')) return;

        // ── Globally disabled via config ──────────────────────────
        if (window._AI_ASSISTANT_ENABLED === false) {
            // Disabled: no widget, no FAB, no session — but keep
            // homepage blur logic working via home.html inline code.
            return;
        }

        // ── Widget UI disabled (floating FAB + window hidden, but  ─
        // homepage inline chat stays fully functional).             ─
        var widgetOn = (window._AI_ASSISTANT_WIDGET_ENABLED !== false);

        if (widgetOn) {
            buildWidget();
            bindWidgetEvents();
        }
        _setupRouterHooks(widgetOn);
        if (widgetOn) renderFocusTags();

        if (_checkIsHomepage()) {
            // ── Homepage: inline chat only. No FAB, no widget. ──
            if (widgetOn) {
                var fab = document.getElementById('aiFab');
                if (fab) fab.style.display = 'none';
            }
            _initHomepageHistory();
            if (!currentSessionId) { autoCreateSession(); }
            else { renderHomepageResponse(); }
            return;
        }

        // ── Non-homepage, widget disabled: only create session ───
        // (so homepage chat can resume when user navigates back).
        if (!widgetOn) {
            if (!currentSessionId) { autoCreateSession(); }
            return;
        }

        // ── Non-homepage: widget mode ──
        if (!currentSessionId) { autoCreateSession(); }
        else { restoreWidgetSession(currentSessionId); }

        // Auto-open from homepage flag (SPA navigation or SSR cross-page)
        var fromHome = localStorage.getItem('ai_from_homepage') === 'true';
        if (fromHome && currentSessionId && !isOpen) {
            localStorage.removeItem('ai_from_homepage');
            open();
            return;
        }

        if (isOpen) {
            var inp = document.getElementById('aiChatInput');
            if (inp) inp.focus();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
