// BubbleEvent Frontend JavaScript

// ═══════════════════════ SIDEBAR ═══════════════════════════════════════
function initSidebar() {
    // Set active nav item based on current path
    const path = window.location.pathname;
    const navItems = document.querySelectorAll('.sidebar-nav-item');

    navItems.forEach(item => {
        const route = item.getAttribute('data-route');
        if (route && (path === route || path.startsWith(route + '/') || path.startsWith(route + '?'))) {
            item.classList.add('active');
        }
    });

    // Root path → home dashboard
    if (!document.querySelector('.sidebar-nav-item.active') && (path === '/' || path === '')) {
        const homeItem = document.querySelector('.sidebar-nav-item[data-route="/home"]');
        if (homeItem) homeItem.classList.add('active');
    }
}

function toggleSidebar() {
    const sidebar = document.getElementById('appSidebar');
    const overlay = document.getElementById('sidebarOverlay');
    if (!sidebar) return;

    const isOpen = sidebar.classList.contains('open');

    if (isOpen) {
        sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('show');
    } else {
        sidebar.classList.add('open');
        if (overlay) overlay.classList.add('show');
    }
}

// Close sidebar when a nav item is clicked (mobile)
document.addEventListener('click', function(e) {
    const navLink = e.target.closest('.sidebar-nav-item');
    if (navLink && window.innerWidth <= 768) {
        // Close immediately — page navigation handles the transition
        const sidebar = document.getElementById('appSidebar');
        const overlay = document.getElementById('sidebarOverlay');
        if (sidebar) sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('show');
    }
});

// ── Auto-refresh ─────────────────────────────────────────────────────
(function() {
    if (window.location.pathname === '/events') {
        const REFRESH_INTERVAL = 5 * 60 * 1000;
        let countdown = REFRESH_INTERVAL / 1000;

        // Clean up any existing auto-refresh interval (SPA re-navigation)
        if (window._autoRefreshId) { clearInterval(window._autoRefreshId); }

        const nav = document.querySelector('.app-topbar .topbar-actions');
        if (nav) {
            const indicator = document.createElement('span');
            indicator.id = 'refresh-indicator';
            indicator.className = 'badge bg-success me-2';
            indicator.textContent = '实时';
            indicator.style.fontSize = '0.7rem';
            nav.prepend(indicator);

            window._autoRefreshId = setInterval(() => {
                countdown -= 10;
                if (countdown <= 0) location.reload();
            }, 10000);
        }
    }
})();

// ═══════════════════════ THEME ═══════════════════════════════════════════
const THEME_KEY = 'bubbleevent-theme';

function getTheme() {
    return localStorage.getItem(THEME_KEY) || 'dark';
}

function setTheme(theme) {
    localStorage.setItem(THEME_KEY, theme);
    applyTheme(theme);
}

function applyTheme(theme) {
    const html = document.documentElement;
    if (theme === 'light') {
        html.setAttribute('data-theme', 'light');
        html.setAttribute('data-bs-theme', 'light');
    } else {
        html.removeAttribute('data-theme');
        html.setAttribute('data-bs-theme', 'dark');
    }
    // Dispatch event so ECharts instances can update
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme } }));
}

function toggleTheme() {
    const current = getTheme();
    const next = current === 'light' ? 'dark' : 'light';
    setTheme(next);
}

function initTheme() {
    const saved = getTheme();
    applyTheme(saved);
}

// ── Bootstrap tooltips ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
    initTheme();
    initSidebar();

    const tooltipTriggers = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggers.map(el => new bootstrap.Tooltip(el));

    // Initialize global search if present
    initGlobalSearch();

    // Animate stat counters if present on page
    if (document.querySelector('.stat-value.num')) {
        animateCounters();
    }
});

// ── Global search ────────────────────────────────────────────────────
function initGlobalSearch() {
    const searchInput = document.getElementById('globalSearch');
    if (!searchInput) return;

    let searchTimeout;
    const resultsDropdown = document.getElementById('searchResults');
    const searchUrl = searchInput.dataset.searchUrl || '/api/v1/events';

    searchInput.addEventListener('input', function() {
        clearTimeout(searchTimeout);
        const query = this.value.trim();

        if (query.length < 2) {
            if (resultsDropdown) resultsDropdown.innerHTML = '';
            if (resultsDropdown) resultsDropdown.classList.add('d-none');
            return;
        }

        searchTimeout = setTimeout(() => performSearch(query, searchUrl, resultsDropdown), 300);
    });

    // Close search results on outside click
    // Clean up previous listener to prevent accumulation (SPA re-navigation)
    if (window._searchClickHandler) {
        document.removeEventListener('click', window._searchClickHandler);
    }
    window._searchClickHandler = function(e) {
        if (resultsDropdown && !searchInput.contains(e.target) && !resultsDropdown.contains(e.target)) {
            resultsDropdown.classList.add('d-none');
        }
    };
    document.addEventListener('click', window._searchClickHandler);

    // Keyboard navigation
    searchInput.addEventListener('keydown', function(e) {
        if (!resultsDropdown || resultsDropdown.classList.contains('d-none')) return;
        const items = resultsDropdown.querySelectorAll('.search-result-item');
        if (!items.length) return;

        const current = resultsDropdown.querySelector('.search-result-item.active');
        let idx = -1;
        if (current) idx = [...items].indexOf(current);

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            idx = (idx + 1) % items.length;
            items.forEach(i => i.classList.remove('active'));
            items[idx].classList.add('active');
            items[idx].scrollIntoView({block: 'nearest'});
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            idx = idx <= 0 ? items.length - 1 : idx - 1;
            items.forEach(i => i.classList.remove('active'));
            items[idx].classList.add('active');
            items[idx].scrollIntoView({block: 'nearest'});
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (current) current.click();
        } else if (e.key === 'Escape') {
            resultsDropdown.classList.add('d-none');
        }
    });
}

async function performSearch(query, url, resultsDropdown) {
    if (!resultsDropdown) return;

    try {
        const resp = await fetch(`${url}?q=${encodeURIComponent(query)}&per_page=8`);
        const data = await resp.json();

        if (!data.success || !data.data.length) {
            resultsDropdown.innerHTML = '<div class="search-result-item text-muted">未找到相关结果</div>';
            resultsDropdown.classList.remove('d-none');
            return;
        }

        var safeQuery = escHtml(query);
        resultsDropdown.innerHTML = data.data.map(item => {
            var safeTitle = escHtml(item.title || '');
            var title = safeTitle.replace(
                new RegExp('(' + safeQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi'),
                '<span class="highlight">$1</span>'
            );
            var safeSummary = escHtml((item.summary || '').substring(0, 80));
            return `
                <a href="/events/${item.event_id || item.id}" class="search-result-item d-block text-decoration-none text-dark">
                    <div class="fw-semibold small">${title}</div>
                    <div class="text-muted small">${safeSummary}</div>
                </a>`;
        }).join('');
        resultsDropdown.classList.remove('d-none');
    } catch(err) {
        resultsDropdown.innerHTML = '<div class="search-result-item text-danger">搜索异常</div>';
        resultsDropdown.classList.remove('d-none');
    }
}

// ── SSE helper (with exponential backoff reconnection) ────────────────
function createSSEConnection(url, onMessage, onError, onDone) {
    var reconnectDelay = 1000;    // start at 1s
    var maxReconnectDelay = 30000; // cap at 30s
    var es = null;
    var aborted = false;

    function connect() {
        if (aborted) return;
        es = new EventSource(url);

        es.onmessage = function(event) {
            reconnectDelay = 1000; // reset on successful message
            try {
                var data = JSON.parse(event.data);
                onMessage(data);
            } catch(e) {
                onMessage({text: event.data});
            }
        };

        es.onerror = function(event) {
            if (aborted) return;
            // Server closed connection — attempt reconnect with backoff
            if (es.readyState === EventSource.CLOSED) {
                es.close();
                if (onError) onError(event);
                setTimeout(function() {
                    connect();
                    reconnectDelay = Math.min(reconnectDelay * 2, maxReconnectDelay);
                }, reconnectDelay);
            } else {
                if (onError) onError(event);
            }
        };

        es.addEventListener('done', function() {
            aborted = true;
            es.close();
            if (onDone) onDone();
        });
    }

    connect();

    return {
        close: function() {
            aborted = true;
            if (es) es.close();
        }
    };
}

// ── Loading state manager ────────────────────────────────────────────
const LoadingState = {
    show: function(containerId, message) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">加载中...</span>
                </div>
                ${message ? `<p class="text-muted mt-2">${message}</p>` : ''}
            </div>`;
    },
    hide: function(containerId) {
        // No-op — caller replaces content
    },
    button: function(btn, loadingText) {
        const origText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> ${loadingText || '处理中...'}`;
        return function restore() {
            btn.disabled = false;
            btn.innerHTML = origText;
        };
    }
};

// ── Toast notification ───────────────────────────────────────────────
function _ensureToastContainer() {
    if (document.getElementById('toastContainer')) return;
    var div = document.createElement('div');
    div.id = 'toastContainer';
    div.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    document.body.appendChild(div);
}

function showNotification(message, type) {
    type = type || 'info'; // success | danger | warning | info
    _ensureToastContainer();

    const container = document.getElementById('toastContainer');
    const id = 'toast-' + Date.now();
    const bgMap = {success: 'bg-success', danger: 'bg-danger', warning: 'bg-warning', info: 'bg-info'};

    const html = `
        <div id="${id}" class="toast align-items-center text-white ${bgMap[type] || 'bg-info'} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>`;
    container.insertAdjacentHTML('beforeend', html);

    const toastEl = document.getElementById(id);
    const toast = new bootstrap.Toast(toastEl, {delay: 4000});
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

// ── HTML escape ─────────────────────────────────────────────────────
function escHtml(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

// ── Format helpers ───────────────────────────────────────────────────
function timeAgo(dateStr) {
    const now = new Date();
    const date = new Date(dateStr);
    const seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + '分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + '小时前';
    return Math.floor(seconds / 86400) + '天前';
}

function formatNumber(num) {
    if (num >= 1e8) return (num / 1e8).toFixed(2) + '亿';
    if (num >= 1e4) return (num / 1e4).toFixed(2) + '万';
    return num !== undefined && num !== null ? num.toLocaleString() : '--';
}

function formatPercent(num) {
    if (num === undefined || num === null) return '--';
    const s = (num > 0 ? '+' : '') + num.toFixed(2) + '%';
    return `<span class="${num > 0 ? 'text-danger' : num < 0 ? 'text-success' : ''}">${s}</span>`;
}

// ── Chart Resize Manager (ResizeObserver-based, replaces window resize) ──
const ChartResizeManager = {
    _entries: new Map(),  // dom -> { observer, chart }

    /**
     * Start observing a DOM element. When it resizes, the chart is resized.
     * Debounced at 100ms to avoid excessive calls during drag/zoom.
     */
    observe: function(chart, dom) {
        if (!dom) return;
        // Remove any existing observer on this dom
        this.unobserve(dom);

        var debounceTimer;
        var observer = new ResizeObserver(function(entries) {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function() {
                for (var i = 0; i < entries.length; i++) {
                    if (chart && !chart.isDisposed()) {
                        try { chart.resize(); } catch(e) {}
                    }
                }
            }, 100);
        });

        observer.observe(dom);
        this._entries.set(dom, { observer: observer, chart: chart });
    },

    /**
     * Stop observing a DOM element. Does NOT dispose the chart itself.
     */
    unobserve: function(dom) {
        if (!dom) return;
        var entry = this._entries.get(dom);
        if (entry) {
            entry.observer.disconnect();
            this._entries.delete(dom);
        }
    },

    /**
     * Stop observing AND dispose the chart. Preferred cleanup method.
     */
    dispose: function(chart, dom) {
        if (dom) this.unobserve(dom);
        if (chart && !chart.isDisposed()) {
            chart.dispose();
        }
    },

    /**
     * Dispose all tracked charts and disconnect all observers.
     * Call on page unload to prevent "message port closed" errors.
     */
    disposeAll: function() {
        this._entries.forEach(function(entry, dom) {
            try { entry.observer.disconnect(); } catch(e) {}
            if (entry.chart && !entry.chart.isDisposed()) {
                try { entry.chart.dispose(); } catch(e) {}
            }
        });
        this._entries.clear();
    },

};

// ── Chart helpers ────────────────────────────────────────────────────
// Theme-aware chart defaults
const ECHARTS_THEME = {
    dark: {
        backgroundColor: 'transparent',
        textStyle: { color: '#99a3b6' },
        legend: { textStyle: { color: '#99a3b6' } },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        axisLabel: { color: '#99a3b6' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
        tooltipBg: 'rgba(24,30,40,0.95)',
        tooltipBorder: 'rgba(255,255,255,0.1)',
        tooltipText: '#e6e9ef',
    },
    light: {
        backgroundColor: 'transparent',
        textStyle: { color: '#4a5568' },
        legend: { textStyle: { color: '#4a5568' } },
        axisLine: { lineStyle: { color: 'rgba(0,0,0,0.12)' } },
        axisLabel: { color: '#4a5568' },
        splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
        tooltipBg: 'rgba(255,255,255,0.95)',
        tooltipBorder: 'rgba(0,0,0,0.1)',
        tooltipText: '#1a1d23',
    }
};

function getCurrentTheme() {
    return document.documentElement.hasAttribute('data-theme') ? 'light' : 'dark';
}

function getChartTheme() {
    return ECHARTS_THEME[getCurrentTheme()] || ECHARTS_THEME.dark;
}

function applyThemeToOption(option) {
    if (!option) return option;
    var t = getChartTheme();
    if (!option.backgroundColor) option.backgroundColor = t.backgroundColor;
    if (option.xAxis) {
        var axes = Array.isArray(option.xAxis) ? option.xAxis : [option.xAxis];
        axes.forEach(function(ax) {
            if (!ax.axisLine) ax.axisLine = t.axisLine;
            if (!ax.axisLabel) ax.axisLabel = t.axisLabel;
            if (!ax.splitLine) ax.splitLine = t.splitLine;
        });
    }
    if (option.yAxis) {
        var axes = Array.isArray(option.yAxis) ? option.yAxis : [option.yAxis];
        axes.forEach(function(ax) {
            if (!ax.axisLine) ax.axisLine = t.axisLine;
            if (!ax.axisLabel) ax.axisLabel = t.axisLabel;
            if (!ax.splitLine) ax.splitLine = t.splitLine;
        });
    }
    if (option.tooltip && !option.tooltip.backgroundColor) {
        option.tooltip.backgroundColor = t.tooltipBg;
        option.tooltip.borderColor = t.tooltipBorder;
        option.tooltip.textStyle = { color: t.tooltipText };
    }
    return option;
}

// Legacy wrapper for backward compatibility (calls new function)
function applyDarkTheme(option) {
    return applyThemeToOption(option);
}

function createECharts(domId, option) {
    var dom = document.getElementById(domId);
    if (!dom) return null;
    var chart = echarts.init(dom);
    chart.setOption(applyThemeToOption(option));
    ChartResizeManager.observe(chart, dom);

    // Listen for theme changes to update chart
    var themeHandler = function() {
        if (chart && !chart.isDisposed()) {
            chart.setOption(applyThemeToOption(option));
        }
    };
    window.addEventListener('themechange', themeHandler);
    // Store handler for cleanup
    chart._themeHandler = themeHandler;

    return chart;
}

function disposeChart(chart, dom) {
    if (chart && chart._themeHandler) {
        window.removeEventListener('themechange', chart._themeHandler);
    }
    if (dom) {
        ChartResizeManager.dispose(chart, dom);
    } else if (chart) {
        chart.dispose();
    }
}

// ── Global Pipeline Trigger ──────────────────────────────────────────
async function triggerPipelineGlobal(force) {
    if (force === undefined) force = false;
    const btn = document.getElementById('btnPipelineTrigger');
    if (!btn) return;

    const origHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 启动中...';

    try {
        const resp = await fetch('/api/v1/pipeline/trigger', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({force: force})
        });
        const data = await resp.json();
        const runId = data.run_id;

        if (data.status !== 'started' || !runId) {
            throw new Error('Pipeline start failed');
        }

        let elapsed = 0;
        const pollInterval = 2000;
        const maxWait = 300000;

        while (elapsed < maxWait) {
            await new Promise(function(r) { setTimeout(r, pollInterval); });
            elapsed += pollInterval;

            const statusResp = await fetch('/api/v1/pipeline/status/' + runId);
            const run = await statusResp.json();

            if (run.status === 'completed') {
                var m = run.result.metadata || {};
                showNotification(
                    '采集完成！文章: ' + (m.articles_collected || 0) +
                    ', 事件: ' + (m.events_extracted || 0) +
                    ', 卡片: ' + (m.cards_generated || 0),
                    'success'
                );
                if (window._router) window._router.reload();
                else location.reload();
                return;
            }

            if (run.status === 'failed') {
                throw new Error(run.progress || 'Pipeline failed');
            }

            var secs = Math.round(elapsed / 1000);
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 处理中... (' + secs + 's)';
        }

        throw new Error('Pipeline timed out after 5 minutes');

    } catch (err) {
        showNotification('采集失败: ' + err.message, 'danger');
    }
    btn.disabled = false;
    btn.innerHTML = origHTML;
}

// ── Counter animation ────────────────────────────────────────────────
function animateCounters() {
    document.querySelectorAll('.stat-value.num').forEach(el => {
        const text = el.textContent.trim();
        if (text === '--' || text === '暂无' || text === '待触发') return;
        const finalVal = parseInt(text.replace(/[^0-9]/g, ''));
        if (isNaN(finalVal)) return;
        const duration = 800;
        const start = performance.now();
        function step(ts) {
            const p = Math.min((ts - start) / duration, 1);
            const eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
            el.textContent = Math.floor(eased * finalVal);
            if (p < 1) requestAnimationFrame(step);
            else el.textContent = finalVal;
        }
        requestAnimationFrame(step);
    });
}

// ── Global: generate briefing (available from any page) ─────────────
// Defined globally so the topbar button works even after SPA navigation
// away from the briefing page (where it's also defined in-page).
async function generateBriefing() {
    const btn = document.getElementById('genBtn');
    const datePicker = document.getElementById('datePicker');
    const dateStr = datePicker ? datePicker.value : new Date().toISOString().slice(0, 10);

    // If not on briefing page, redirect there
    if (!btn || !datePicker) {
        window.location.href = '/briefing?date=' + dateStr;
        return;
    }

    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>生成中…';
    try {
        const resp = await fetch('/api/v1/briefing/generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({date: dateStr})
        });
        const data = await resp.json();
        if (data.success) {
            location.reload();
        } else {
            showNotification('生成失败: ' + (data.error || '未知错误'), 'danger');
        }
    } catch(e) {
        showNotification('请求失败: ' + e.message, 'danger');
    }
    btn.disabled = false;
    btn.innerHTML = original;
}
