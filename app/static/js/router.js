/**
 * BubbleEvent — Client-side SPA Router
 * Intercepts sidebar navigation, fetches page fragments, swaps content area.
 * Vanilla JS, zero dependencies. Works alongside existing SSR for initial load.
 */
(function() {
    'use strict';

    // ═══════════════════════ ROUTE MAP ═════════════════════════════════
    const ROUTE_MAP = {
        '/':                '/api/v1/fragment/home',
        '/home':            '/api/v1/fragment/home',
    };

    // Dynamic route patterns
    const DYNAMIC_ROUTES = [
        { pattern: /^\/events\/([a-zA-Z0-9_-]+)$/, fragment: '/api/v1/fragment/events/$1' },
    ];

    // Static routes mapped by prefix
    const STATIC_PREFIXES = [
        '/events', '/sources', '/prediction', '/briefing', '/timeline', '/assistant'
    ];

    function _fragmentUrl(path) {
        // Check exact match
        if (ROUTE_MAP[path]) return ROUTE_MAP[path];

        // Check dynamic patterns
        for (var i = 0; i < DYNAMIC_ROUTES.length; i++) {
            var m = path.match(DYNAMIC_ROUTES[i].pattern);
            if (m) return DYNAMIC_ROUTES[i].fragment.replace('$1', m[1]);
        }

        // Check static prefixes
        for (var j = 0; j < STATIC_PREFIXES.length; j++) {
            if (path.startsWith(STATIC_PREFIXES[j])) {
                return '/api/v1/fragment' + path;
            }
        }

        return null;
    }

    // ═══════════════════════ STATE ════════════════════════════════════
    var _currentPath = window.location.pathname + window.location.search;
    var _isNavigating = false;
    var _cache = {};  // fragmentUrl -> {html, title, ts}

    // ═══════════════════════ NAVIGATION EVENT EMITTER ═══════════════════
    var _listeners = {
        beforeNavigate: [],
        afterNavigate: []
    };

    function _emit(eventName, data) {
        (_listeners[eventName] || []).forEach(function(fn) {
            try { fn(data); } catch(e) { console.warn('nav hook error (' + eventName + '):', e); }
        });
    }

    // ═══════════════════════ DOM REFS ═════════════════════════════════
    function _appContent() { return document.getElementById('appContent'); }
    function _topbarTitle() { return document.getElementById('topbarTitle'); }

    // ═══════════════════════ PROGRESS BAR ═════════════════════════════
    var _progressBar = null;
    function _ensureProgressBar() {
        if (_progressBar) return;
        _progressBar = document.createElement('div');
        _progressBar.id = 'spaProgressBar';
        _progressBar.style.cssText = 'position:fixed;top:0;left:0;height:2px;background:var(--accent,#d4a853);z-index:9999;width:0;transition:width 0.3s ease;box-shadow:0 0 8px var(--accent-glow,rgba(212,168,83,0.4));';
        document.body.appendChild(_progressBar);
    }
    function _showProgress() { _ensureProgressBar(); _progressBar.style.width = '70%'; _progressBar.style.opacity = '1'; }
    function _hideProgress() { if (_progressBar) { _progressBar.style.width = '100%'; setTimeout(function() { _progressBar.style.opacity = '0'; setTimeout(function() { _progressBar.style.width = '0'; }, 300); }, 150); } }

    // ═══════════════════════ SCRIPT EXECUTION ═════════════════════════
    function _executeScripts(container) {
        var scripts = container.querySelectorAll('script');
        scripts.forEach(function(oldScript) {
            var newScript = document.createElement('script');
            // Copy attributes
            Array.from(oldScript.attributes).forEach(function(attr) {
                newScript.setAttribute(attr.name, attr.value);
            });
            // Always wrap in IIFE for scope isolation — prevents const/let
            // re-declaration errors when the same fragment is navigated to
            // multiple times. Global function declarations still work because
            // they're assigned to window properties via hoisting when declared
            // with `function name(){}` syntax (not const/let).
            newScript.textContent = '(function(){\n' + oldScript.textContent + '\n})();';
            oldScript.parentNode.replaceChild(newScript, oldScript);
        });
    }

    // ═══════════════════════ PAGE CLEANUP ═════════════════════════════
    function _cleanupPage() {
        // Call page-specific destroy hook
        if (typeof window._pageDestroy === 'function') {
            try { window._pageDestroy(); } catch(e) { console.warn('_pageDestroy error:', e); }
        }
        window._pageDestroy = null;

        // ── Document-level listener cleanup convention ────────────────
        // Page scripts that add document/window event listeners should
        // push a cleanup function to window._pageCleanups so they are
        // automatically removed on SPA navigation. Example:
        //   var handler = function(e) { ... };
        //   document.addEventListener('keydown', handler);
        //   window._pageCleanups.push(function() {
        //       document.removeEventListener('keydown', handler);
        //   });
        if (Array.isArray(window._pageCleanups)) {
            window._pageCleanups.forEach(function(fn) {
                try { fn(); } catch(e) { console.warn('page cleanup error:', e); }
            });
        }
        window._pageCleanups = [];

        // Dispose all tracked ECharts instances
        if (typeof ChartResizeManager !== 'undefined') {
            try { ChartResizeManager.disposeAll(); } catch(e) {}
        }

        // Remove any lingering modals/overlays
        var overlays = document.querySelectorAll('.modal-backdrop, #nodeDetailFullscreen');
        overlays.forEach(function(el) { el.remove(); });
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
    }

    // ═══════════════════════ NAVIGATION ═══════════════════════════════
    async function navigate(url, addToHistory) {
        if (_isNavigating) return;
        if (addToHistory === undefined) addToHistory = true;

        // Normalize URL
        var a = document.createElement('a');
        a.href = url;
        var path = a.pathname + a.search;

        // Don't navigate to same page
        if (path === _currentPath && addToHistory) return;

        var fragmentUrl = _fragmentUrl(path);
        if (!fragmentUrl) {
            // Not an SPA-managed route — fall back to full navigation
            window.location.href = url;
            return;
        }

        _isNavigating = true;

        // Update active sidebar item
        _updateSidebarActive(path);

        // Show loading indicator
        _showProgress();

        // Fire before-navigate hook (old page DOM still intact — capture rects, etc.)
        var _isLeavingHomepage = !!document.querySelector('.page-home');
        var _isEnteringHomepage = (path === '/' || path === '');
        _emit('beforeNavigate', {
            from: _currentPath,
            to: path,
            isLeavingHomepage: _isLeavingHomepage,
            isEnteringHomepage: _isEnteringHomepage
        });

        // Cleanup current page
        _cleanupPage();

        // Scroll to top
        window.scrollTo({ top: 0, behavior: 'auto' });

        try {
            // Check cache (5 second TTL for freshness)
            var cached = _cache[fragmentUrl];
            var now = Date.now();
            var result;
            if (cached && (now - cached.ts) < 5000) {
                result = { html: cached.html, title: cached.title };
            } else {
                var resp = await fetch(fragmentUrl);
                result = await resp.json();
                // Cache it
                _cache[fragmentUrl] = { html: result.html, title: result.title, ts: now };
            }

            // Update document title
            document.title = result.title || 'BubbleEvent';

            // Update topbar breadcrumb
            var topbar = _topbarTitle();
            if (topbar) topbar.textContent = result.title || 'BubbleEvent';

            // Swap content
            var appContent = _appContent();
            if (appContent) {
                appContent.innerHTML = result.html;
                _executeScripts(appContent);
            }

            // Fire after-navigate hook (new page DOM ready — execute animations, etc.)
            _emit('afterNavigate', {
                from: _currentPath,
                to: path,
                isLeavingHomepage: _isLeavingHomepage,
                isEnteringHomepage: _isEnteringHomepage
            });

            // Update URL
            if (addToHistory) {
                window.history.pushState({ path: path, fragment: true }, result.title || '', path);
            }
            _currentPath = path;

            _hideProgress();
        } catch(e) {
            console.error('SPA navigation failed:', e);
            // Fall back to full page navigation
            window.location.href = url;
            return;
        }

        _isNavigating = false;
    }

    function reload() {
        // Re-navigate to current path (force refresh, skip cache)
        var fragmentUrl = _fragmentUrl(_currentPath);
        if (fragmentUrl && _cache[fragmentUrl]) {
            delete _cache[fragmentUrl];
        }
        navigate(_currentPath, false);
    }

    // ═══════════════════════ SIDEBAR ACTIVE ═══════════════════════════
    function _updateSidebarActive(path) {
        var navItems = document.querySelectorAll('.sidebar-nav-item');
        navItems.forEach(function(item) {
            item.classList.remove('active');
            var route = item.getAttribute('data-route');
            if (route) {
                if (route === '/home' && (path === '/' || path === '')) {
                    item.classList.add('active');
                } else if (route !== '/home' && path.startsWith(route)) {
                    item.classList.add('active');
                }
            }
        });
    }

    // ═══════════════════════ LINK INTERCEPTION ════════════════════════
    function _shouldIntercept(a) {
        // Only intercept internal links
        if (!a.href) return false;
        if (a.hostname !== window.location.hostname) return false;
        // Respect explicit opt-out
        if (a.hasAttribute('data-no-spa')) return false;
        // Always intercept data-spa links
        if (a.hasAttribute('data-spa')) return true;
        // Don't intercept links with target="_blank"
        if (a.target === '_blank') return false;
        // Don't intercept download links or mailto
        if (a.hasAttribute('download') || a.href.startsWith('mailto:') || a.href.startsWith('javascript:')) return false;
        // Don't intercept if modifier keys are pressed
        // (handled in event listener, but filter here too)
        // Only intercept sidebar nav and in-page links
        var inSidebar = a.closest('.sidebar-nav');
        var inContent = a.closest('#appContent');
        return !!(inSidebar || (inContent && a.hasAttribute('data-spa')));
    }

    document.addEventListener('click', function(e) {
        // Respect modifier keys (open in new tab, etc.)
        if (e.ctrlKey || e.metaKey || e.shiftKey) return;
        if (e.defaultPrevented) return;

        var a = e.target.closest('a');
        if (!a) return;
        if (!_shouldIntercept(a)) return;

        e.preventDefault();
        navigate(a.href, true);
    });

    // ═══════════════════════ POPSTATE (BACK/FORWARD) ══════════════════
    window.addEventListener('popstate', function(e) {
        if (e.state && e.state.fragment) {
            navigate(e.state.path || window.location.pathname + window.location.search, false);
        } else if (e.state && e.state.path) {
            navigate(e.state.path, false);
        } else {
            // No state — do full page navigation
            window.location.reload();
        }
    });

    // ═══════════════════════ INITIAL STATE ════════════════════════════
    // Push initial state so back button works from the start
    if (!window.history.state || !window.history.state.fragment) {
        window.history.replaceState(
            { path: _currentPath, fragment: true },
            document.title,
            window.location.href
        );
    }

    // ═══════════════════════ EXPORT ══════════════════════════════════
    window._router = {
        navigate: navigate,
        reload: reload,
        get currentPath() { return _currentPath; },
        onBeforeNavigate: function(fn) { _listeners.beforeNavigate.push(fn); },
        onAfterNavigate: function(fn) { _listeners.afterNavigate.push(fn); },
    };

})();
