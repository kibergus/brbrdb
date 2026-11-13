/*
 * Copyright 2026 Alexey Guseynov (kibergus). All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * ==============================================================================
 */

/**
 * 2-Level Plot-Group Toolbar (`PlotTab` + `FolderMenu` + `•••` Overflow).
 *
 * Resolves built-in session channels and Companion custom `plot_groups` / `plot_layout`
 * into single-line top-level tabs, domain folder popovers, and a unified responsive `•••` menu.
 */
import { state } from './state.js';
import { isChannelAvailable, togglePlot } from './plots_sync.js';

export const BUILTIN_PLOT_GROUPS = [
    { id: 'pedals', label: 'Pedals' },
    { id: 'steering', label: 'Steering' },
    { id: 'rps', label: 'RPS' },
    { id: 'gforce', label: 'G-Force' },
    { id: 'gyro', label: 'Gyro' },
    { id: 'slide', label: 'Slide' },
    { id: 'patch_vel', label: 'Patch Vel' },
    { id: 'force', label: 'Force' },
    { id: 'tyre_load', label: 'Tyre Load' },
    { id: 'slip_angle', label: 'Slip Angle' },
    { id: 'eeg', label: 'EEG' },
    { id: 'ecg', label: 'ECG' },
    { id: 'emg', label: 'EMG' }
];

export const DEFAULT_PLOT_LAYOUT = [
    'pedals',
    'steering',
    'slip_angle',
    {
        folder: 'tyres',
        label: 'Tyres',
        items: ['tyre_load', 'slide', 'patch_vel', 'force']
    },
    {
        folder: 'bio',
        label: 'Bio Sensors',
        items: ['ecg', 'emg']
    },
    '*',
    {
        folder: 'more',
        label: '•••',
        overflow: true,
        items: ['gforce', 'rps', 'eeg']
    }
];

const GROUP_ID_ALIASES = {
    'g_force': 'gforce',
    'g-force': 'gforce',
    'lat_force': 'force',
    'long_force': 'force',
    'lat_patch_vel': 'patch_vel',
    'long_patch_vel': 'patch_vel',
    'tyreload': 'tyre_load',
    'slipangle': 'slip_angle'
};

/**
 * Normalizes minor plot-group ID aliases (`g_force` -> `gforce`, `lat_force` -> `force`, etc.).
 * @param {string} rawId
 * @returns {string}
 */
export function normalizePlotGroupId(rawId) {
    if (rawId === undefined || rawId === null) return '';
    const cleaned = String(rawId).trim();
    if (!cleaned) return '';
    if (cleaned === '*') return '*';
    const lowered = cleaned.toLowerCase();
    return GROUP_ID_ALIASES[lowered] || lowered;
}

/**
 * Formats the visible label on a `FolderMenu` button, including active child count badge.
 * @param {{ folder: string, label: string, overflow?: boolean }} folder
 * @param {number} activeCount
 * @returns {string}
 */
export function formatFolderButtonText(folder, activeCount) {
    const base = folder.label || (folder.overflow ? '•••' : folder.folder);
    if (folder.overflow) {
        return activeCount > 0 ? `${base} · ${activeCount}` : base;
    }
    return activeCount > 0 ? `${base} · ${activeCount} ▾` : `${base} ▾`;
}

/**
 * Deterministic 6-step toolbar resolution algorithm.
 * Never drops any available plot group even if `"*"` is omitted from a custom layout.
 *
 * @param {Array<{id: string, label: string, folder?: string, folder_label?: string, isCustom?: boolean}>} availableGroups
 * @param {Array<string | object> | null | undefined} manifestPlotLayout
 * @returns {Array<object>}
 */
export function resolvePlotToolbar(availableGroups = [], manifestPlotLayout = null) {
    // 1. Select base layout
    const baseLayout = (Array.isArray(manifestPlotLayout) && manifestPlotLayout.length > 0)
        ? manifestPlotLayout
        : DEFAULT_PLOT_LAYOUT;

    // 2. Build availableMap in registration order
    const availableMap = new Map();
    for (const raw of availableGroups || []) {
        if (!raw || !raw.id) continue;
        const id = normalizePlotGroupId(raw.id);
        if (!id || id === '*' || id === 'speed' || id === 'delta') continue;
        availableMap.set(id, {
            id,
            label: raw.label ? String(raw.label) : id.toUpperCase(),
            folder: raw.folder ? String(raw.folder).trim().toLowerCase() : '',
            folder_label: raw.folder_label ? String(raw.folder_label).trim() : '',
            isCustom: Boolean(raw.isCustom)
        });
    }

    if (availableMap.size === 0) {
        return [];
    }

    // 3. Walk layout & filter to available
    const placedIds = new Set();
    let sawWildcard = false;
    const rawTopEntries = [];
    const folderEntriesBySlug = new Map();

    for (const entry of baseLayout) {
        if (typeof entry === 'string') {
            const norm = normalizePlotGroupId(entry);
            if (!norm) continue;
            if (norm === '*') {
                if (!sawWildcard) {
                    sawWildcard = true;
                    rawTopEntries.push({ type: 'wildcard' });
                }
            } else if (availableMap.has(norm) && !placedIds.has(norm)) {
                placedIds.add(norm);
                rawTopEntries.push({ type: 'tab', group: availableMap.get(norm) });
            }
        } else if (entry && typeof entry === 'object' && entry.folder) {
            const folderSlug = String(entry.folder).trim().toLowerCase();
            if (!folderSlug) continue;
            const folderLabel = entry.label ? String(entry.label).trim() : folderSlug;
            const isOverflow = Boolean(entry.overflow);
            const unwrapSingle = entry.unwrap_single !== false;
            const rawItems = [];

            if (Array.isArray(entry.items)) {
                for (const rawItem of entry.items) {
                    const normItem = normalizePlotGroupId(rawItem);
                    if (!normItem) continue;
                    if (normItem === '*') {
                        if (!sawWildcard) {
                            sawWildcard = true;
                            rawItems.push('*');
                        }
                    } else if (availableMap.has(normItem) && !placedIds.has(normItem)) {
                        placedIds.add(normItem);
                        rawItems.push(normItem);
                    }
                }
            }

            const folderEntry = {
                type: 'folder',
                folder: folderSlug,
                label: folderLabel,
                overflow: isOverflow,
                unwrap_single: unwrapSingle,
                rawItems
            };
            rawTopEntries.push(folderEntry);
            if (!folderEntriesBySlug.has(folderSlug)) {
                folderEntriesBySlug.set(folderSlug, folderEntry);
            }
        }
    }

    // 4. Bundle unplaced shorthand folders & collect unplaced standalone groups
    const remainderEntries = [];
    const synthFolders = new Map();

    for (const group of availableMap.values()) {
        if (placedIds.has(group.id)) continue;
        placedIds.add(group.id);

        if (group.folder) {
            // If a folder with the same slug already exists in base layout, merge into it
            const existingLayoutFolder = folderEntriesBySlug.get(group.folder);
            if (existingLayoutFolder) {
                if (group.folder_label && existingLayoutFolder.label === existingLayoutFolder.folder) {
                    existingLayoutFolder.label = group.folder_label;
                }
                const starIdx = existingLayoutFolder.rawItems.indexOf('*');
                if (starIdx >= 0) {
                    existingLayoutFolder.rawItems.splice(starIdx, 0, group.id);
                } else {
                    existingLayoutFolder.rawItems.push(group.id);
                }
                continue;
            }

            if (!synthFolders.has(group.folder)) {
                const synth = {
                    type: 'folder',
                    folder: group.folder,
                    label: group.folder_label || group.folder,
                    overflow: group.folder === 'more',
                    unwrap_single: true,
                    rawItems: [group.id]
                };
                synthFolders.set(group.folder, synth);
                remainderEntries.push(synth);
            } else {
                const synth = synthFolders.get(group.folder);
                if (group.folder_label && synth.label === synth.folder) {
                    synth.label = group.folder_label;
                }
                synth.rawItems.push(group.id);
            }
        } else {
            remainderEntries.push({ type: 'tab', group });
        }
    }

    // 5. Expand "*" remainder (or safety-net fallback into overflow "•••" folder)
    const expandedTopEntries = [];
    let remainderHandled = remainderEntries.length === 0;

    for (const entry of rawTopEntries) {
        if (entry.type === 'wildcard') {
            for (const rem of remainderEntries) {
                expandedTopEntries.push(rem);
            }
            remainderHandled = true;
            continue;
        }

        if (entry.type === 'folder') {
            const starIdx = entry.rawItems.indexOf('*');
            if (starIdx >= 0) {
                const flattenedIds = [];
                for (const rem of remainderEntries) {
                    if (rem.type === 'tab') {
                        flattenedIds.push(rem.group.id);
                    } else if (rem.type === 'folder') {
                        flattenedIds.push(...rem.rawItems);
                    }
                }
                entry.rawItems.splice(starIdx, 1, ...flattenedIds);
                remainderHandled = true;
            }
        }

        expandedTopEntries.push(entry);
    }

    // Safety net: if "*" was omitted from a custom layout, append remaining items to overflow "•••"
    if (!remainderHandled) {
        const flattenedIds = [];
        for (const rem of remainderEntries) {
            if (rem.type === 'tab') {
                flattenedIds.push(rem.group.id);
            } else if (rem.type === 'folder') {
                flattenedIds.push(...rem.rawItems);
            }
        }
        let overflowTarget = expandedTopEntries.find((e) => e.type === 'folder' && e.overflow);
        if (!overflowTarget) {
            overflowTarget = {
                type: 'folder',
                folder: 'more',
                label: '•••',
                overflow: true,
                unwrap_single: false,
                rawItems: []
            };
            expandedTopEntries.push(overflowTarget);
        }
        overflowTarget.rawItems.push(...flattenedIds);
    }

    // 6. Prune empty folders & auto-unwrap 1-item non-overflow folders
    const resolved = [];
    for (const entry of expandedTopEntries) {
        if (entry.type === 'tab') {
            resolved.push({
                type: 'tab',
                id: entry.group.id,
                label: entry.group.label,
                group: entry.group
            });
            continue;
        }

        if (entry.type === 'folder') {
            const items = [];
            const seenInFolder = new Set();
            for (const itemId of entry.rawItems) {
                if (itemId === '*' || seenInFolder.has(itemId)) continue;
                const g = availableMap.get(itemId);
                if (g) {
                    seenInFolder.add(itemId);
                    items.push(g);
                }
            }

            if (items.length === 0) {
                continue;
            }

            if (!entry.overflow && entry.unwrap_single !== false && items.length === 1) {
                const single = items[0];
                resolved.push({
                    type: 'tab',
                    id: single.id,
                    label: single.label,
                    group: single
                });
            } else {
                resolved.push({
                    type: 'folder',
                    folder: entry.folder,
                    label: entry.label,
                    overflow: Boolean(entry.overflow),
                    unwrap_single: entry.unwrap_single !== false,
                    items,
                    sections: items.map((g) => ({ type: 'item', group: g }))
                });
            }
        }
    }

    return resolved;
}

function buildCombinedOverflowFolder(explicitMore, spilledPrimary) {
    const explicitItems = explicitMore ? explicitMore.items : [];
    const sections = [];
    const allItems = [];
    const seenIds = new Set();

    const pushUniqueItem = (g) => {
        if (g && !seenIds.has(g.id)) {
            seenIds.add(g.id);
            allItems.push(g);
        }
    };

    for (const spilled of spilledPrimary) {
        if (spilled.type === 'tab') {
            sections.push({ type: 'item', group: spilled.group });
            pushUniqueItem(spilled.group);
        } else if (spilled.type === 'folder') {
            sections.push({ type: 'header', label: spilled.label });
            for (const child of spilled.items) {
                sections.push({ type: 'item', group: child, indented: true });
                pushUniqueItem(child);
            }
        }
    }

    if (spilledPrimary.length > 0 && explicitItems.length > 0) {
        sections.push({ type: 'divider' });
    }

    for (const item of explicitItems) {
        sections.push({ type: 'item', group: item });
        pushUniqueItem(item);
    }

    if (allItems.length === 0) {
        return null;
    }

    return {
        type: 'folder',
        folder: explicitMore ? explicitMore.folder : 'more',
        label: explicitMore ? explicitMore.label : '•••',
        overflow: true,
        unwrap_single: false,
        items: allItems,
        sections
    };
}

/**
 * Computes which resolved top-level items stay visible and which spill right-to-left
 * into the `•••` overflow menu for a given container width.
 *
 * @param {Array<object>} resolvedItems
 * @param {number} containerWidth
 * @param {(item: object) => number} getItemWidth
 * @param {number} moreWidth
 * @param {number} gap
 * @returns {Array<object>}
 */
export function computeResponsiveToolbarItems(
    resolvedItems,
    containerWidth,
    getItemWidth,
    moreWidth = 52,
    gap = 6
) {
    if (!Array.isArray(resolvedItems) || resolvedItems.length === 0) {
        return [];
    }

    const primaryItems = [];
    let explicitMore = null;
    for (const item of resolvedItems) {
        if (item.type === 'folder' && item.overflow) {
            if (!explicitMore) {
                explicitMore = item;
            } else {
                // Merge multiple overflow folders if ever supplied
                explicitMore = buildCombinedOverflowFolder(explicitMore, [
                    ...item.items.map((g) => ({ type: 'tab', group: g }))
                ]);
            }
        } else {
            primaryItems.push(item);
        }
    }

    const hasExplicitMore = Boolean(explicitMore && explicitMore.items.length > 0);
    if (!containerWidth || containerWidth <= 0 || primaryItems.length === 0) {
        const result = [...primaryItems];
        const ov = buildCombinedOverflowFolder(explicitMore, []);
        if (ov) result.push(ov);
        return result;
    }

    const itemWidths = primaryItems.map((it) => Math.max(24, Number(getItemWidth(it)) || 72));
    const sumPrimaryWidth = (count) => {
        let sum = 0;
        for (let i = 0; i < count; i++) {
            sum += itemWidths[i];
        }
        if (count > 1) {
            sum += (count - 1) * gap;
        }
        return sum;
    };

    const fullWidthNeeded = sumPrimaryWidth(primaryItems.length) +
        (hasExplicitMore ? (primaryItems.length > 0 ? gap : 0) + moreWidth : 0);

    if (fullWidthNeeded <= containerWidth) {
        const result = [...primaryItems];
        const ov = buildCombinedOverflowFolder(explicitMore, []);
        if (ov) result.push(ov);
        return result;
    }

    // Spill trailing primary items right-to-left until remaining items + "•••" fit
    let keepCount = 0;
    for (let c = primaryItems.length - 1; c >= 0; c--) {
        const needed = sumPrimaryWidth(c) + (c > 0 ? gap : 0) + moreWidth;
        if (needed <= containerWidth) {
            keepCount = c;
            break;
        }
    }

    const visiblePrimary = primaryItems.slice(0, keepCount);
    const spilledPrimary = primaryItems.slice(keepCount);
    const overflowFolder = buildCombinedOverflowFolder(explicitMore, spilledPrimary);

    return overflowFolder ? [...visiblePrimary, overflowFolder] : visiblePrimary;
}

// ---------------------------------------------------------------------------
// DOM Controller State
// ---------------------------------------------------------------------------

const TOOLBAR_GAP_PX = 6;
const _staticButtonsByContainer = new WeakMap();
const _measuredWidths = new Map();

let _openFolderSlug = null;
let _popoverEl = null;
let _activeFolderModels = new Map();
let _resizeObserver = null;
let _observedContainer = null;
const _boundDocuments = new WeakSet();
let _lastContainerWidth = 0;

export function estimateButtonWidth(text) {
    const len = String(text || '').length;
    return Math.max(42, Math.ceil(len * 7.2) + 20);
}

function getActiveCountForFolder(folder) {
    if (!folder || !Array.isArray(folder.items) || !state.activePlotChannels) return 0;
    let count = 0;
    for (const g of folder.items) {
        if (state.activePlotChannels.has(g.id)) count++;
    }
    return count;
}

function getItemDisplayText(item) {
    if (item.type === 'tab') {
        return item.label || item.id.toUpperCase();
    }
    const activeCount = getActiveCountForFolder(item);
    return formatFolderButtonText(item, activeCount);
}

function getCachedOrEstimatedWidth(item) {
    const text = getItemDisplayText(item);
    const key = `${item.type}:${item.type === 'tab' ? item.id : item.folder}:${text}`;
    if (_measuredWidths.has(key)) {
        return _measuredWidths.get(key);
    }
    return estimateButtonWidth(text);
}

function recordMeasuredElementWidth(item, el) {
    if (!el) return;
    let measured = 0;
    if (typeof el.getBoundingClientRect === 'function') {
        const rect = el.getBoundingClientRect();
        if (rect && rect.width > 0) {
            measured = Math.ceil(rect.width);
        }
    } else if (typeof el._measuredWidth === 'number' && el._measuredWidth > 0) {
        measured = Math.ceil(el._measuredWidth);
    }
    if (measured > 0) {
        const text = getItemDisplayText(item);
        const key = `${item.type}:${item.type === 'tab' ? item.id : item.folder}:${text}`;
        _measuredWidths.set(key, measured);
    }
}

function captureStaticButtons(tabsContainer) {
    if (!tabsContainer) return new Map();
    let map = _staticButtonsByContainer.get(tabsContainer);
    if (!map) {
        map = new Map();
        _staticButtonsByContainer.set(tabsContainer, map);
    }
    const children = Array.isArray(tabsContainer.children)
        ? [...tabsContainer.children]
        : Array.from(tabsContainer.children || []);
    for (const child of children) {
        if (!child || typeof child.getAttribute !== 'function') continue;
        if (child.getAttribute('data-toolbar-managed') === '1') continue;
        if (String(child.className || '').includes('dynamic-plot-tab')) continue;
        const rawTab = child.getAttribute('data-tab');
        if (!rawTab) continue;
        const normId = normalizePlotGroupId(rawTab);
        if (!normId || map.has(normId)) continue;
        const defaultLabel = child.getAttribute('data-default-label') || child.textContent || normId.toUpperCase();
        if (typeof child.setAttribute === 'function') {
            child.setAttribute('data-default-label', defaultLabel);
            child.setAttribute('data-toolbar-managed', '1');
        }
        map.set(normId, { el: child, defaultLabel });
    }
    return map;
}

export function collectAvailablePlotGroups(tabsContainer = null) {
    const staticMap = tabsContainer ? captureStaticButtons(tabsContainer) : new Map();
    const customGroups = state.customPlotGroups instanceof Map ? state.customPlotGroups : new Map();
    const groups = [];
    const seen = new Set();

    for (const builtin of BUILTIN_PLOT_GROUPS) {
        if (!isChannelAvailable(builtin.id)) continue;
        const customOverride = customGroups.get(builtin.id);
        const staticInfo = staticMap.get(builtin.id);
        const defaultLabel = staticInfo ? staticInfo.defaultLabel : builtin.label;
        groups.push({
            id: builtin.id,
            label: customOverride
                ? (customOverride.label || builtin.id.toUpperCase())
                : defaultLabel,
            folder: customOverride && customOverride.folder ? customOverride.folder : '',
            folder_label: customOverride && customOverride.folder_label ? customOverride.folder_label : '',
            isCustom: Boolean(customOverride)
        });
        seen.add(builtin.id);
    }

    for (const [rawId, customDef] of customGroups.entries()) {
        const id = normalizePlotGroupId(rawId);
        if (!id || seen.has(id) || id === 'speed' || id === 'delta') continue;
        seen.add(id);
        groups.push({
            id,
            label: customDef.label ? String(customDef.label) : id.toUpperCase(),
            folder: customDef.folder ? String(customDef.folder).trim().toLowerCase() : '',
            folder_label: customDef.folder_label ? String(customDef.folder_label).trim() : '',
            isCustom: true
        });
    }

    return groups;
}

function nodeContains(root, target) {
    if (!root || !target) return false;
    if (root === target) return true;
    if (typeof root.contains === 'function') {
        return root.contains(target);
    }
    let cur = target.parentNode || null;
    while (cur) {
        if (cur === root) return true;
        cur = cur.parentNode || null;
    }
    return false;
}

function getPopoverMountRoot(tabsContainer) {
    if (typeof document !== 'undefined' && document.body && typeof document.body.appendChild === 'function') {
        return document.body;
    }
    if (tabsContainer && tabsContainer.parentNode && typeof tabsContainer.parentNode.appendChild === 'function') {
        return tabsContainer.parentNode;
    }
    return tabsContainer;
}

function positionOpenPopover() {
    if (!_popoverEl || !_openFolderSlug || typeof document === 'undefined') return;
    const anchorBtn = document.getElementById(`folder-btn-${_openFolderSlug}`);
    if (!anchorBtn || typeof anchorBtn.getBoundingClientRect !== 'function') return;

    const btnRect = anchorBtn.getBoundingClientRect();
    const popRect = typeof _popoverEl.getBoundingClientRect === 'function'
        ? _popoverEl.getBoundingClientRect()
        : null;
    const popWidth = (popRect && popRect.width > 0) ? popRect.width : (_popoverEl.offsetWidth || 200);
    const popHeight = (popRect && popRect.height > 0) ? popRect.height : (_popoverEl.offsetHeight || 180);

    const vw = (typeof window !== 'undefined' && window.innerWidth) ? window.innerWidth : 1024;
    const vh = (typeof window !== 'undefined' && window.innerHeight) ? window.innerHeight : 768;

    const folderModel = _activeFolderModels.get(_openFolderSlug);
    const alignRight = Boolean(folderModel && folderModel.overflow);
    const desiredLeft = alignRight ? (btnRect.right - popWidth) : btnRect.left;
    const clampedLeft = Math.max(8, Math.min(desiredLeft, Math.max(8, vw - popWidth - 8)));

    const spaceBelow = vh - btnRect.bottom - 10;
    const spaceAbove = btnRect.top - 10;
    let top;
    if (spaceBelow >= popHeight || spaceBelow >= spaceAbove) {
        top = Math.min(btnRect.bottom + 6, Math.max(8, vh - popHeight - 8));
    } else {
        top = Math.max(8, btnRect.top - popHeight - 6);
    }

    _popoverEl.style.left = `${Math.round(clampedLeft)}px`;
    _popoverEl.style.top = `${Math.round(top)}px`;
}

function clearElementChildren(el) {
    if (!el) return;
    while (el.children && el.children.length > 0) {
        const child = el.children[0];
        if (child && typeof child.remove === 'function') {
            child.remove();
        } else {
            el.children.shift();
        }
    }
}

function renderOpenPopoverContents(tabsContainer) {
    if (!_openFolderSlug || typeof document === 'undefined') return;
    const folderModel = _activeFolderModels.get(_openFolderSlug);
    if (!folderModel || !Array.isArray(folderModel.items) || folderModel.items.length === 0) {
        closePlotFolderPopover();
        return;
    }

    const mountRoot = getPopoverMountRoot(tabsContainer);
    if (!mountRoot) return;

    if (!_popoverEl) {
        _popoverEl = document.createElement('div');
        _popoverEl.id = 'plot-folder-popover';
        _popoverEl.className = 'plot-folder-popover';
        _popoverEl.setAttribute('role', 'menu');
    }
    _popoverEl.setAttribute('data-folder', _openFolderSlug);
    clearElementChildren(_popoverEl);

    const sections = Array.isArray(folderModel.sections) && folderModel.sections.length > 0
        ? folderModel.sections
        : folderModel.items.map((g) => ({ type: 'item', group: g }));

    for (const entry of sections) {
        if (entry.type === 'divider') {
            const div = document.createElement('div');
            div.className = 'plot-folder-divider';
            _popoverEl.appendChild(div);
            continue;
        }
        if (entry.type === 'header') {
            const hdr = document.createElement('div');
            hdr.className = 'plot-folder-section-header';
            hdr.textContent = entry.label;
            _popoverEl.appendChild(hdr);
            continue;
        }
        if (entry.type === 'item' && entry.group) {
            const group = entry.group;
            const isActive = Boolean(state.activePlotChannels && state.activePlotChannels.has(group.id));
            const itemBtn = document.createElement('button');
            itemBtn.type = 'button';
            itemBtn.id = `folder-item-${group.id}`;
            itemBtn.className = 'plot-folder-item' +
                (isActive ? ' active' : '') +
                (entry.indented ? ' indented' : '');
            itemBtn.setAttribute('role', 'menuitemcheckbox');
            itemBtn.setAttribute('aria-checked', isActive ? 'true' : 'false');
            itemBtn.setAttribute('data-tab', group.id);

            const checkSpan = document.createElement('span');
            checkSpan.className = 'plot-folder-check';
            checkSpan.textContent = isActive ? '✓' : '';

            const labelSpan = document.createElement('span');
            labelSpan.className = 'plot-folder-item-label';
            labelSpan.textContent = group.label;

            itemBtn.appendChild(checkSpan);
            itemBtn.appendChild(labelSpan);

            itemBtn.addEventListener('click', (e) => {
                if (e && typeof e.stopPropagation === 'function') {
                    e.stopPropagation();
                }
                togglePlot(group.id);
            });

            _popoverEl.appendChild(itemBtn);
        }
    }

    if (_popoverEl.parentNode !== mountRoot) {
        mountRoot.appendChild(_popoverEl);
    }

    positionOpenPopover();
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => positionOpenPopover());
    }
}

export function closePlotFolderPopover() {
    if (_popoverEl && typeof _popoverEl.remove === 'function') {
        _popoverEl.remove();
    }
    _popoverEl = null;
    const prevSlug = _openFolderSlug;
    _openFolderSlug = null;

    if (prevSlug && typeof document !== 'undefined' && typeof document.getElementById === 'function') {
        const btn = document.getElementById(`folder-btn-${prevSlug}`);
        if (btn) {
            btn.classList.remove('open');
            if (typeof btn.setAttribute === 'function') {
                btn.setAttribute('aria-expanded', 'false');
            }
        }
    }
}

export function openPlotFolderPopover(folderSlug) {
    if (!folderSlug || !_activeFolderModels.has(folderSlug)) {
        closePlotFolderPopover();
        return;
    }
    if (_openFolderSlug === folderSlug) {
        closePlotFolderPopover();
        return;
    }

    if (_openFolderSlug && typeof document !== 'undefined' && typeof document.getElementById === 'function') {
        const prevBtn = document.getElementById(`folder-btn-${_openFolderSlug}`);
        if (prevBtn) {
            prevBtn.classList.remove('open');
            if (typeof prevBtn.setAttribute === 'function') {
                prevBtn.setAttribute('aria-expanded', 'false');
            }
        }
    }

    _openFolderSlug = folderSlug;
    const tabsContainer = (typeof document !== 'undefined' && typeof document.querySelector === 'function')
        ? document.querySelector('.plot-tabs')
        : null;

    if (typeof document !== 'undefined' && typeof document.getElementById === 'function') {
        const nextBtn = document.getElementById(`folder-btn-${folderSlug}`);
        if (nextBtn) {
            nextBtn.classList.add('open');
            if (typeof nextBtn.setAttribute === 'function') {
                nextBtn.setAttribute('aria-expanded', 'true');
            }
        }
    }

    renderOpenPopoverContents(tabsContainer);
}

function ensureGlobalDismissListeners() {
    if (typeof document === 'undefined' || !document || typeof document.addEventListener !== 'function') return;
    if (_boundDocuments.has(document)) return;
    _boundDocuments.add(document);

    document.addEventListener('click', (e) => {
        if (!_openFolderSlug) return;
        const target = e ? e.target : null;
        if (_popoverEl && nodeContains(_popoverEl, target)) return;
        const activeBtn = typeof document.getElementById === 'function'
            ? document.getElementById(`folder-btn-${_openFolderSlug}`)
            : null;
        if (activeBtn && nodeContains(activeBtn, target)) return;
        closePlotFolderPopover();
    });

    document.addEventListener('keydown', (e) => {
        if (_openFolderSlug && e && e.key === 'Escape') {
            closePlotFolderPopover();
        }
    });

    if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
        window.addEventListener('resize', () => {
            if (_openFolderSlug) {
                positionOpenPopover();
            }
        });
    }
}

function ensureResizeObserver(tabsContainer) {
    if (!tabsContainer || typeof ResizeObserver !== 'function') return;
    if (_observedContainer === tabsContainer && _resizeObserver) return;

    if (_resizeObserver) {
        try {
            _resizeObserver.disconnect();
        } catch {
            // Ignore disconnect errors
        }
    }

    _observedContainer = tabsContainer;
    _lastContainerWidth = tabsContainer.clientWidth || 0;
    _resizeObserver = new ResizeObserver((entries) => {
        const entry = entries && entries[0];
        const nextWidth = Math.round(
            (entry && entry.contentRect ? entry.contentRect.width : tabsContainer.clientWidth) || 0
        );
        if (nextWidth > 0 && Math.abs(nextWidth - _lastContainerWidth) >= 2) {
            _lastContainerWidth = nextWidth;
            renderPlotToolbar(nextWidth);
        }
    });
    _resizeObserver.observe(tabsContainer);
}

/**
 * Renders (or re-renders) the 2-level plot toolbar into `.plot-tabs`.
 * @param {number} [widthOverride]
 */
export function renderPlotToolbar(widthOverride = undefined) {
    if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return [];
    const tabsContainer = document.querySelector('.plot-tabs');
    if (!tabsContainer) return [];

    ensureGlobalDismissListeners();
    ensureResizeObserver(tabsContainer);

    const staticMap = captureStaticButtons(tabsContainer);
    const availableGroups = collectAvailablePlotGroups(tabsContainer);
    const resolvedItems = resolvePlotToolbar(availableGroups, state.customPlotLayout);

    const containerWidth = widthOverride !== undefined
        ? Number(widthOverride)
        : Number(tabsContainer.clientWidth || 0);
    if (containerWidth > 0) {
        _lastContainerWidth = containerWidth;
    }

    const explicitMore = resolvedItems.find((it) => it.type === 'folder' && it.overflow);
    const moreSample = explicitMore || { folder: 'more', label: '•••', overflow: true, items: [] };
    const moreWidth = getCachedOrEstimatedWidth({
        type: 'folder',
        folder: moreSample.folder,
        label: moreSample.label,
        overflow: true,
        items: moreSample.items
    });

    const visibleItems = computeResponsiveToolbarItems(
        resolvedItems,
        containerWidth,
        (it) => getCachedOrEstimatedWidth(it),
        moreWidth,
        TOOLBAR_GAP_PX
    );

    clearElementChildren(tabsContainer);
    _activeFolderModels = new Map();

    const customGroups = state.customPlotGroups instanceof Map ? state.customPlotGroups : new Map();

    for (const item of visibleItems) {
        if (item.type === 'tab') {
            const staticEntry = staticMap.get(item.id);
            let btn = staticEntry ? staticEntry.el : null;
            if (!btn) {
                btn = document.createElement('button');
                btn.type = 'button';
                btn.id = `tab-btn-${item.id}`;
                btn.className = 'plot-tab';
                btn.setAttribute('data-tab', item.id);
                btn.setAttribute('data-toolbar-managed', '1');
            }

            if (!btn._plotToolbarBound) {
                const targetId = item.id;
                btn.addEventListener('click', () => {
                    closePlotFolderPopover();
                    togglePlot(targetId);
                });
                btn._plotToolbarBound = true;
            }

            const isCustomOverride = customGroups.has(item.id);
            if (staticEntry && typeof btn.setAttribute === 'function') {
                if (isCustomOverride) {
                    btn.setAttribute('data-custom-override', '1');
                } else if (typeof btn.removeAttribute === 'function') {
                    btn.removeAttribute('data-custom-override');
                } else {
                    btn.setAttribute('data-custom-override', '');
                }
            }

            const isActive = Boolean(state.activePlotChannels && state.activePlotChannels.has(item.id));
            btn.textContent = item.label;
            btn.style.display = '';
            btn.classList.toggle('active', isActive);
            tabsContainer.appendChild(btn);
            recordMeasuredElementWidth(item, btn);
        } else if (item.type === 'folder') {
            _activeFolderModels.set(item.folder, item);
            const activeCount = getActiveCountForFolder(item);
            const isOpen = _openFolderSlug === item.folder;

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.id = `folder-btn-${item.folder}`;
            btn.className = 'plot-tab plot-folder-btn' +
                (item.overflow ? ' plot-overflow-btn' : '') +
                (activeCount > 0 ? ' active' : '') +
                (isOpen ? ' open' : '');
            btn.setAttribute('data-folder', item.folder);
            btn.setAttribute('data-toolbar-managed', '1');
            btn.setAttribute('aria-haspopup', 'true');
            btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            btn.textContent = formatFolderButtonText(item, activeCount);

            const folderSlug = item.folder;
            btn.addEventListener('click', (e) => {
                if (e && typeof e.stopPropagation === 'function') {
                    e.stopPropagation();
                }
                openPlotFolderPopover(folderSlug);
            });

            tabsContainer.appendChild(btn);
            recordMeasuredElementWidth(item, btn);
        }
    }

    if (_openFolderSlug) {
        if (_activeFolderModels.has(_openFolderSlug)) {
            renderOpenPopoverContents(tabsContainer);
        } else {
            closePlotFolderPopover();
        }
    }

    return visibleItems;
}

/**
 * Synchronizes active highlights, folder child count badges (`TYRES · 2 ▾`, `••• · 1`),
 * and open popover checkmarks whenever `state.activePlotChannels` changes.
 */
export function syncPlotToolbarActiveState() {
    if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return;
    const tabsContainer = document.querySelector('.plot-tabs');
    if (!tabsContainer) return;

    const children = Array.isArray(tabsContainer.children)
        ? tabsContainer.children
        : Array.from(tabsContainer.children || []);

    // If toolbar hasn't rendered managed items yet, leave static buttons synced by data-tab
    for (const child of children) {
        if (!child || typeof child.getAttribute !== 'function') continue;
        const tabId = child.getAttribute('data-tab');
        if (tabId) {
            const normId = normalizePlotGroupId(tabId);
            const isActive = Boolean(state.activePlotChannels && state.activePlotChannels.has(normId));
            child.classList.toggle('active', isActive);
            continue;
        }
        const folderSlug = child.getAttribute('data-folder');
        if (folderSlug && _activeFolderModels.has(folderSlug)) {
            const model = _activeFolderModels.get(folderSlug);
            const activeCount = getActiveCountForFolder(model);
            child.classList.toggle('active', activeCount > 0);
            child.textContent = formatFolderButtonText(model, activeCount);
        }
    }

    if (_openFolderSlug) {
        renderOpenPopoverContents(tabsContainer);
    }
}

/**
 * Initializes the plot toolbar on page startup.
 */
export function initPlotToolbar() {
    renderPlotToolbar();
}
