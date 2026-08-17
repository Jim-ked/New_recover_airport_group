const SUPPORTED_PATHS = new Set(['/situations', '/base-data']);
const loadedAssets = new Map();

let currentWorkspace = null;
let navigationController = null;
let navigationSequence = 0;
let restoringHistory = false;

function workspaceRoot(documentRoot = document) {
  return documentRoot.querySelector('main[data-workspace][data-workspace-module]');
}

function supportedUrl(value) {
  const url = new URL(value, window.location.href);
  return url.origin === window.location.origin && SUPPORTED_PATHS.has(url.pathname) ? url : null;
}

function assetAlreadyPresent(url, kind) {
  const selector = kind === 'style' ? 'link[rel="stylesheet"][href]' : 'script[src]';
  return [...document.querySelectorAll(selector)].some((element) => {
    const value = kind === 'style' ? element.href : element.src;
    return value === url;
  });
}

function loadAsset(asset, pageUrl) {
  const kind = asset.dataset.workspaceAsset;
  const attribute = kind === 'style' ? 'href' : 'src';
  const source = asset.getAttribute(attribute);
  if (!source || !['style', 'script'].includes(kind)) return Promise.resolve();
  const url = new URL(source, pageUrl).href;
  if (assetAlreadyPresent(url, kind)) return Promise.resolve();
  if (loadedAssets.has(url)) return loadedAssets.get(url);

  const pending = new Promise((resolve, reject) => {
    const element = kind === 'style' ? document.createElement('link') : document.createElement('script');
    if (kind === 'style') {
      element.rel = 'stylesheet';
      element.href = url;
    } else {
      element.src = url;
    }
    element.dataset.workspaceAsset = kind;
    element.addEventListener('load', resolve, { once: true });
    element.addEventListener('error', () => reject(new Error(`Workspace asset failed: ${url}`)), { once: true });
    document.head.append(element);
  });
  loadedAssets.set(url, pending);
  return pending;
}

async function loadWorkspaceAssets(parsedDocument, pageUrl) {
  for (const asset of parsedDocument.querySelectorAll('[data-workspace-asset]')) {
    await loadAsset(asset, pageUrl);
  }
}

function updateSidebar(pathname) {
  for (const link of document.querySelectorAll('[data-workspace-link]')) {
    const active = new URL(link.href, window.location.href).pathname === pathname
      && link.classList.contains('nav');
    if (link.classList.contains('nav')) link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}

function replaceWorkspaceChrome(parsedDocument, nextRoot, targetUrl) {
  const currentRoot = workspaceRoot();
  const currentContext = document.querySelector('.topbar-page-context');
  const nextContext = parsedDocument.querySelector('.topbar-page-context');
  if (!currentRoot || !currentContext || !nextContext) throw new Error('Workspace shell contract is incomplete.');

  currentContext.replaceChildren(
    ...[...nextContext.childNodes].map((node) => document.importNode(node, true)),
  );
  currentRoot.replaceWith(nextRoot);
  document.title = parsedDocument.title;
  updateSidebar(targetUrl.pathname);
}

async function navigate(value, { historyMode = 'push', historyIndex = null } = {}) {
  const targetUrl = supportedUrl(value);
  if (!targetUrl || !currentWorkspace) return false;
  if (targetUrl.href === currentWorkspace.url.href) {
    navigationController?.abort();
    navigationController = null;
    navigationSequence += 1;
    return true;
  }

  if (await currentWorkspace.module.beforeLeave?.() === false) {
    if (historyMode === 'pop' && Number.isInteger(historyIndex)) {
      const delta = currentWorkspace.historyIndex - historyIndex;
      if (delta) {
        restoringHistory = true;
        history.go(delta);
      }
    }
    return false;
  }

  navigationController?.abort();
  navigationController = new AbortController();
  const sequence = ++navigationSequence;

  try {
    const response = await fetch(targetUrl, {
      credentials: 'same-origin',
      headers: { Accept: 'text/html' },
      signal: navigationController.signal,
    });
    if (!response.ok) throw new Error(`Workspace navigation failed (${response.status}).`);
    const parsedDocument = new DOMParser().parseFromString(await response.text(), 'text/html');
    const parsedRoot = workspaceRoot(parsedDocument);
    if (!parsedRoot) throw new Error('Target page is not a supported workspace.');

    await loadWorkspaceAssets(parsedDocument, targetUrl);
    const moduleUrl = new URL(parsedRoot.dataset.workspaceModule, targetUrl).href;
    const nextModule = await import(moduleUrl);
    if (sequence !== navigationSequence) return false;

    const nextRoot = document.importNode(parsedRoot, true);
    currentWorkspace.module.unmount?.();
    replaceWorkspaceChrome(parsedDocument, nextRoot, targetUrl);
    await nextModule.mount(nextRoot, { url: targetUrl.href });
    if (sequence !== navigationSequence) return false;

    let nextHistoryIndex = historyIndex;
    if (historyMode === 'push') {
      nextHistoryIndex = currentWorkspace.historyIndex + 1;
      history.pushState({ ...history.state, workspaceIndex: nextHistoryIndex }, '', targetUrl);
    }
    currentWorkspace = {
      root: nextRoot,
      module: nextModule,
      url: targetUrl,
      historyIndex: Number.isInteger(nextHistoryIndex) ? nextHistoryIndex : currentWorkspace.historyIndex,
    };
    navigationController = null;
    return true;
  } catch (error) {
    if (error.name === 'AbortError') return false;
    console.error(error);
    window.location.assign(targetUrl.href);
    return false;
  }
}

async function mountInitialWorkspace() {
  const root = workspaceRoot();
  const initialUrl = supportedUrl(window.location.href);
  if (!root || !initialUrl) return;
  const moduleUrl = new URL(root.dataset.workspaceModule, initialUrl).href;
  const pageModule = await import(moduleUrl);
  const historyIndex = Number.isInteger(history.state?.workspaceIndex)
    ? history.state.workspaceIndex
    : 0;
  history.replaceState({ ...history.state, workspaceIndex: historyIndex }, '', initialUrl);
  currentWorkspace = { root, module: pageModule, url: initialUrl, historyIndex };
  await pageModule.mount(root, { url: initialUrl.href });

  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-workspace-link]');
    if (!link || event.defaultPrevented || event.button !== 0
      || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
      || link.target || link.hasAttribute('download')) return;
    const targetUrl = supportedUrl(link.href);
    if (!targetUrl) return;
    event.preventDefault();
    navigate(targetUrl);
  });

  window.addEventListener('popstate', (event) => {
    if (restoringHistory) {
      restoringHistory = false;
      return;
    }
    const targetUrl = supportedUrl(window.location.href);
    if (!targetUrl) return;
    navigate(targetUrl, {
      historyMode: 'pop',
      historyIndex: Number.isInteger(event.state?.workspaceIndex)
        ? event.state.workspaceIndex
        : currentWorkspace.historyIndex,
    });
  });
}

mountInitialWorkspace().catch((error) => {
  console.error('Unable to mount workspace.', error);
});
