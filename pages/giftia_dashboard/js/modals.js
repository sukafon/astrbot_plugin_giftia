// Giftia Dashboard Modal Actions

window.openModal = function(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add("show");
        document.body.classList.add("modal-open");
    }
};

window.closeModal = function(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove("show");
        // Only remove modal-open class if no other modals are open
        const openedModals = document.querySelectorAll(".modal-overlay.show");
        if (openedModals.length === 0) {
            document.body.classList.remove("modal-open");
        }
    }
};

// Add listener to close modal when clicking outside (on overlay)
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".modal-overlay").forEach(overlay => {
        overlay.addEventListener("click", function(e) {
            // Close if clicking directly on the overlay backdrop
            if (e.target === this) {
                const id = this.id;
                if (id) {
                    window.closeModal(id);
                }
            }
        });
    });
});

function normalizeMemoryImportance(value) {
    let importance = Number(value || 5);
    if (!Number.isFinite(importance)) {
        importance = 5;
    }
    return Math.min(10, Math.max(1, importance));
}

// 1. Add Memory
window.openAddMemoryModal = function() {
    document.getElementById("add-mem-bot").value = "";
    document.getElementById("add-mem-group").value = "";
    document.getElementById("add-mem-associated-ids").value = "";
    document.getElementById("add-mem-importance").value = "5";
    document.getElementById("add-mem-text").value = "";
    window.openModal("add-memory-modal");
};

window.submitAddMemory = async function() {
    const botName = document.getElementById("add-mem-bot").value.trim();
    const groupId = document.getElementById("add-mem-group").value.trim();
    const associatedIds = document.getElementById("add-mem-associated-ids").value.trim();
    const importance = normalizeMemoryImportance(document.getElementById("add-mem-importance").value);
    const text = document.getElementById("add-mem-text").value.trim();

    if (!botName || !groupId || !text) {
        window.showToast("请填写完整参数！");
        return;
    }

    try {
        const res = await window.apiPost("/memories/add", {
            bot_name: botName,
            group_or_user_id: groupId,
            text: text,
            user_id: "admin",
            associated_user_ids: associatedIds,
            importance: importance
        });
        if (res.status === "success") {
            window.showToast("保存记忆成功！");
            window.closeModal("add-memory-modal");
            window.GiftiaApp.loadMemories();
        } else {
            window.showToast(`保存失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

// 2. Edit Memory
window.openEditMemoryModal = function(id, bot, group, textEncoded, associatedIds, importance) {
    const text = decodeURIComponent(textEncoded);
    document.getElementById("edit-mem-id").value = id;
    document.getElementById("edit-mem-bot").value = bot;
    document.getElementById("edit-mem-group").value = group;
    document.getElementById("edit-mem-associated-ids").value = associatedIds || "";
    document.getElementById("edit-mem-importance").value = normalizeMemoryImportance(importance);
    document.getElementById("edit-mem-text").value = text;
    window.openModal("edit-memory-modal");
};

window.submitEditMemory = async function() {
    const id = document.getElementById("edit-mem-id").value;
    const bot = document.getElementById("edit-mem-bot").value;
    const group = document.getElementById("edit-mem-group").value;
    const associatedIds = document.getElementById("edit-mem-associated-ids").value.trim();
    const importance = normalizeMemoryImportance(document.getElementById("edit-mem-importance").value);
    const text = document.getElementById("edit-mem-text").value.trim();

    if (!text) {
        window.showToast("记忆文本不能为空！");
        return;
    }

    try {
        const res = await window.apiPost("/memories/update", {
            memory_id: id,
            bot_name: bot,
            group_or_user_id: group,
            text: text,
            user_id: "admin",
            associated_user_ids: associatedIds,
            importance: importance
        });
        if (res.status === "success") {
            window.showToast("保存成功！");
            window.closeModal("edit-memory-modal");
            window.GiftiaApp.loadMemories();
        } else {
            window.showToast(`更新失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

// 3. Delete Memory
window.deleteMemory = function(id) {
    window.showConfirm("确认删除长期记忆", "确定要删除这条长期记忆吗？此操作无法撤销。", async () => {
        try {
            const res = await window.apiPost("/memories/delete", { memory_id: id });
            if (res.status === "success") {
                window.showToast("记忆删除成功");
                window.GiftiaApp.loadMemories();
            } else {
                window.showToast(`删除失败: ${res.message}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

window.openMemoryCleanModal = function() {
    const botSelect = document.getElementById("memory-bot-name");
    const groupSelect = document.getElementById("memory-group-id");
    document.getElementById("clean-mem-bot").value = botSelect ? botSelect.value : "";
    document.getElementById("clean-mem-group").value = groupSelect ? groupSelect.value : "";
    document.getElementById("clean-mem-associated-user-id").value = document.getElementById("memory-associated-user-id")?.value || "";
    document.getElementById("clean-mem-search").value = document.getElementById("memory-search")?.value || "";
    document.getElementById("clean-mem-max-importance").value = "3";
    document.getElementById("clean-mem-max-hit-count").value = "1";
    document.getElementById("clean-mem-min-age-days").value = "60";
    document.getElementById("clean-mem-last-hit-before-days").value = "30";
    document.getElementById("clean-mem-include-never-hit").checked = true;

    const summary = document.getElementById("memory-clean-summary");
    summary.innerHTML = "设置条件后点击“筛选候选记忆”。";
    summary.style.borderLeftColor = "var(--border-color)";
    document.getElementById("memory-clean-candidate-list").innerHTML = "";
    document.getElementById("memory-clean-selection-tools").style.display = "none";
    document.getElementById("btn-clean-selected-memories").disabled = true;
    setMemoryCleanMode("memory-clean-manual");
    window.openModal("memory-clean-modal");
};

function normalizeMemoryCleanNumber(value, fallback, minValue, maxValue) {
    let result = Number(value);
    if (!Number.isFinite(result)) {
        result = fallback;
    }
    result = Math.trunc(result);
    if (typeof minValue === "number") {
        result = Math.max(minValue, result);
    }
    if (typeof maxValue === "number") {
        result = Math.min(maxValue, result);
    }
    return result;
}

function setMemoryCleanMode(mode) {
    const modal = document.getElementById("memory-clean-modal");
    if (!modal) return;

    modal.querySelectorAll(".media-tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-mediatab") === mode);
    });
    modal.querySelectorAll(".media-tab-panel").forEach(panel => {
        panel.classList.toggle("active", panel.id === `mediatab-${mode}`);
    });

    const manualMode = mode === "memory-clean-manual";
    const manualFilter = document.getElementById("btn-memory-manual-filter");
    const manualClean = document.getElementById("btn-clean-selected-memories");
    const autoTrigger = document.getElementById("btn-auto-clean-mem-trigger");
    const autoSave = document.getElementById("btn-auto-clean-mem-save");
    if (manualFilter) manualFilter.style.display = manualMode ? "inline-block" : "none";
    if (manualClean) manualClean.style.display = manualMode ? "inline-block" : "none";
    if (autoTrigger) autoTrigger.style.display = manualMode ? "none" : "inline-block";
    if (autoSave) autoSave.style.display = manualMode ? "none" : "inline-block";
}

function collectMemoryCleanCriteria() {
    return {
        bot_name: document.getElementById("clean-mem-bot").value.trim(),
        group_or_user_id: document.getElementById("clean-mem-group").value.trim(),
        associated_user_id: document.getElementById("clean-mem-associated-user-id").value.trim(),
        search: document.getElementById("clean-mem-search").value.trim(),
        max_importance: normalizeMemoryCleanNumber(document.getElementById("clean-mem-max-importance").value, 3, 1, 10),
        max_hit_count: normalizeMemoryCleanNumber(document.getElementById("clean-mem-max-hit-count").value, 1, 0),
        min_age_days: normalizeMemoryCleanNumber(document.getElementById("clean-mem-min-age-days").value, 60, 0),
        last_hit_before_days: normalizeMemoryCleanNumber(document.getElementById("clean-mem-last-hit-before-days").value, 30, 0),
        include_never_hit: document.getElementById("clean-mem-include-never-hit").checked,
        limit: 500
    };
}

function renderMemoryCleanCandidates(items, total, truncated) {
    const list = document.getElementById("memory-clean-candidate-list");
    const tools = document.getElementById("memory-clean-selection-tools");
    const summary = document.getElementById("memory-clean-summary");

    if (!items || items.length === 0) {
        list.innerHTML = "";
        tools.style.display = "none";
        summary.innerHTML = "没有筛选到符合条件的长期记忆。";
        summary.style.borderLeftColor = "var(--success)";
        window.updateMemoryCleanSelectionState();
        return;
    }

    const truncationText = truncated ? `，仅显示前 ${items.length} 条` : "";
    summary.innerHTML = `筛选到 <strong>${total}</strong> 条候选记忆${truncationText}。请取消勾选需要保留的记忆后再清理。`;
    summary.style.borderLeftColor = "var(--primary)";
    tools.style.display = "flex";

    list.innerHTML = items.map(item => {
        const reasons = Array.isArray(item.clean_reasons) ? item.clean_reasons : [];
        const reasonHtml = reasons.length > 0
            ? reasons.map(reason => `<span>${window.escapeHtml(reason)}</span>`).join("")
            : "<span>符合当前筛选条件</span>";
        const lastHitAt = item.last_hit_at ? window.formatDate(item.last_hit_at) : "从未命中";
        return `
            <div class="memory-clean-candidate">
                <input type="checkbox" class="memory-clean-checkbox" data-memory-id="${window.escapeHtml(item.memory_id)}" onchange="updateMemoryCleanSelectionState()" checked>
                <div>
                    <div class="memory-clean-candidate-text">${window.escapeHtml(item.text)}</div>
                    <div class="memory-clean-candidate-meta">
                        <span>会话 ${window.escapeHtml(item.group_or_user_id || "-")}</span>
                        <span>重要度 ${item.importance}</span>
                        <span>命中 ${item.hit_count || 0} 次</span>
                        <span>创建 ${window.formatDate(item.created_at)}</span>
                        <span>最近命中 ${lastHitAt}</span>
                    </div>
                    <div class="memory-clean-candidate-reasons">${reasonHtml}</div>
                </div>
            </div>
        `;
    }).join("");
    window.updateMemoryCleanSelectionState();
}

window.loadMemoryCleanCandidates = async function() {
    const criteria = collectMemoryCleanCriteria();
    const summary = document.getElementById("memory-clean-summary");
    if (!criteria.bot_name) {
        window.showToast("请选择或填写 Bot 名称");
        return;
    }

    summary.innerHTML = "正在筛选候选记忆...";
    summary.style.borderLeftColor = "var(--primary)";
    document.getElementById("memory-clean-candidate-list").innerHTML = "";
    document.getElementById("memory-clean-selection-tools").style.display = "none";
    document.getElementById("btn-clean-selected-memories").disabled = true;

    try {
        const res = await window.apiPost("/memories/clean/candidates", criteria);
        if (res.status === "success" && res.data) {
            renderMemoryCleanCandidates(res.data.items, res.data.total, res.data.truncated);
        } else {
            summary.innerHTML = `筛选失败: ${res.message || "请求出错"}`;
            summary.style.borderLeftColor = "var(--danger)";
        }
    } catch (e) {
        summary.innerHTML = `筛选出错: ${e.message}`;
        summary.style.borderLeftColor = "var(--danger)";
    }
};

window.updateMemoryCleanSelectionState = function() {
    const checkboxes = Array.from(document.querySelectorAll("#memory-clean-candidate-list .memory-clean-checkbox"));
    const selected = checkboxes.filter(chk => chk.checked).length;
    const selectedText = document.getElementById("memory-clean-selected-count");
    if (selectedText) {
        selectedText.textContent = `已选择 ${selected} / ${checkboxes.length} 条`;
    }
    const cleanBtn = document.getElementById("btn-clean-selected-memories");
    if (cleanBtn) {
        cleanBtn.disabled = selected === 0;
    }
};

window.toggleAllMemoryCleanCandidates = function(checked) {
    document.querySelectorAll("#memory-clean-candidate-list .memory-clean-checkbox").forEach(chk => {
        chk.checked = checked;
    });
    window.updateMemoryCleanSelectionState();
};

window.invertMemoryCleanCandidates = function() {
    document.querySelectorAll("#memory-clean-candidate-list .memory-clean-checkbox").forEach(chk => {
        chk.checked = !chk.checked;
    });
    window.updateMemoryCleanSelectionState();
};

window.cleanSelectedMemories = function() {
    const selectedIds = Array.from(document.querySelectorAll("#memory-clean-candidate-list .memory-clean-checkbox:checked"))
        .map(chk => chk.getAttribute("data-memory-id"))
        .filter(Boolean);

    if (selectedIds.length === 0) {
        window.showToast("没有选中的记忆");
        return;
    }

    window.showConfirm("确认清理长期记忆", `确定要清理选中的 ${selectedIds.length} 条长期记忆吗？此操作无法撤销。`, async () => {
        try {
            const res = await window.apiPost("/memories/clean", { memory_ids: selectedIds });
            if (res.status === "success") {
                const failedCount = Array.isArray(res.failed_ids) ? res.failed_ids.length : 0;
                const suffix = failedCount > 0 ? `，${failedCount} 条未能删除` : "";
                window.showToast(`已清理 ${res.deleted_count || selectedIds.length} 条长期记忆${suffix}`);
                window.closeModal("memory-clean-modal");
                window.GiftiaApp.resetPagination("memories");
                await window.GiftiaApp.refreshScopedFilters("memories", true);
                await window.GiftiaApp.loadMemories();
            } else {
                window.showToast(`清理失败: ${res.message || "请求出错"}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

window.loadAutoCleanMemoryConfig = async function() {
    const summary = document.getElementById("auto-clean-memory-summary");
    summary.innerHTML = "正在加载自动清理配置...";
    summary.style.borderLeftColor = "var(--primary)";

    try {
        const res = await window.apiGet("/memories/auto_clean/config");
        if (res.status !== "success" || !res.config) {
            throw new Error(res.message || "加载配置失败");
        }

        const cfg = res.config;
        document.getElementById("auto-clean-mem-enabled").checked = Boolean(cfg.enabled);
        document.getElementById("auto-clean-mem-max-importance").value = normalizeMemoryCleanNumber(cfg.max_importance, 3, 1, 7);
        document.getElementById("auto-clean-mem-max-hit-count").value = normalizeMemoryCleanNumber(cfg.max_hit_count, 1, 0);
        document.getElementById("auto-clean-mem-min-age-days").value = normalizeMemoryCleanNumber(cfg.min_age_days, 60, 7);
        document.getElementById("auto-clean-mem-last-hit-before-days").value = normalizeMemoryCleanNumber(cfg.last_hit_before_days, 30, 7);
        document.getElementById("auto-clean-mem-max-delete-per-run").value = normalizeMemoryCleanNumber(cfg.max_delete_per_run, 20, 1, 200);
        document.getElementById("auto-clean-mem-include-never-hit").checked = cfg.include_never_hit !== false;
        summary.innerHTML = "自动清理不会删除重要度 8 以上、创建不足 7 天、最近 7 天命中过的记忆。";
        summary.style.borderLeftColor = "var(--primary)";
    } catch (e) {
        summary.innerHTML = `加载配置失败: ${e.message}`;
        summary.style.borderLeftColor = "var(--danger)";
    }
};

function collectAutoCleanMemoryConfig() {
    return {
        enabled: document.getElementById("auto-clean-mem-enabled").checked,
        max_importance: normalizeMemoryCleanNumber(document.getElementById("auto-clean-mem-max-importance").value, 3, 1, 7),
        max_hit_count: normalizeMemoryCleanNumber(document.getElementById("auto-clean-mem-max-hit-count").value, 1, 0),
        min_age_days: normalizeMemoryCleanNumber(document.getElementById("auto-clean-mem-min-age-days").value, 60, 7),
        last_hit_before_days: normalizeMemoryCleanNumber(document.getElementById("auto-clean-mem-last-hit-before-days").value, 30, 7),
        include_never_hit: document.getElementById("auto-clean-mem-include-never-hit").checked,
        max_delete_per_run: normalizeMemoryCleanNumber(document.getElementById("auto-clean-mem-max-delete-per-run").value, 20, 1, 200),
        cron: "30 3 * * *"
    };
}

window.saveAutoCleanMemoryConfig = async function() {
    const summary = document.getElementById("auto-clean-memory-summary");
    const config = collectAutoCleanMemoryConfig();
    try {
        const res = await window.apiPost("/memories/auto_clean/config", config);
        if (res.status === "success") {
            window.showToast("自动清理配置保存成功！");
            summary.innerHTML = "配置已保存。启用后每天凌晨 03:30 执行自动清理。";
            summary.style.borderLeftColor = "var(--success)";
        } else {
            window.showToast(`保存失败: ${res.message || "请求出错"}`);
        }
    } catch (e) {
        window.showToast(`保存配置出错: ${e.message}`);
    }
};

window.triggerAutoCleanMemoriesImmediately = function() {
    window.showConfirm("确认执行自动清理", "确认要立即按当前自动清理配置清理长期记忆吗？此操作无法撤销。", async () => {
        try {
            await window.apiPost("/memories/auto_clean/config", collectAutoCleanMemoryConfig());
            const res = await window.apiPost("/memories/auto_clean/trigger", {});
            if (res.status === "success") {
                const count = res.deleted_count ?? res.count ?? 0;
                window.showToast(`自动清理完成，共删除 ${count} 条长期记忆`);
                window.closeModal("memory-clean-modal");
                window.GiftiaApp.resetPagination("memories");
                await window.GiftiaApp.refreshScopedFilters("memories", true);
                await window.GiftiaApp.loadMemories();
            } else {
                window.showToast(`执行失败: ${res.message || "请求出错"}`);
            }
        } catch (e) {
            window.showToast(`执行自动清理出错: ${e.message}`);
        }
    });
};

// 4. Edit Media Caption
window.openEditMediaModal = function(hash, urlEncoded, type, captionEncoded, genreEncoded, characterEncoded, sourceEncoded, textEncoded) {
    const url = decodeURIComponent(urlEncoded);
    const caption = decodeURIComponent(captionEncoded);
    const genre = decodeURIComponent(genreEncoded || "");
    const character = decodeURIComponent(characterEncoded || "");
    const source = decodeURIComponent(sourceEncoded || "");
    const text = decodeURIComponent(textEncoded || "");
    
    document.getElementById("edit-media-hash").value = hash;
    document.getElementById("edit-media-caption").value = caption;
    document.getElementById("edit-media-genre").value = genre;
    document.getElementById("edit-media-character").value = character;
    document.getElementById("edit-media-source").value = source;
    document.getElementById("edit-media-text").value = text;

    const titleEl = document.getElementById("edit-media-title");
    if (titleEl) {
        titleEl.textContent = `修改媒体转述描述 (${hash})`;
    }

    const tabsContainer = document.querySelector(".edit-media-tabs");
    if (tabsContainer) {
        tabsContainer.querySelectorAll(".media-tab-btn").forEach(b => {
            if (b.getAttribute("data-mediatab") === "caption") {
                b.classList.add("active");
            } else {
                b.classList.remove("active");
            }
        });
        tabsContainer.querySelectorAll(".media-tab-panel").forEach(p => {
            if (p.id === "mediatab-caption") {
                p.classList.add("active");
            } else {
                p.classList.remove("active");
            }
        });
    }
    
    const previewContainer = document.getElementById("edit-media-preview");
    const gridElId = `media-preview-${hash}`;
    const gridEl = document.getElementById(gridElId);
    const isOriginalLoaded = (type === "image" && window.GiftiaApp.loadedOriginalMediaG && window.GiftiaApp.loadedOriginalMediaG.has(hash)) || 
                             (type !== "image" && gridEl && gridEl.src && gridEl.src.startsWith("data:"));

    if (isOriginalLoaded && gridEl && gridEl.src && gridEl.src.startsWith("data:")) {
        if (type === "image" && url) {
            const uniqueId = `edit-media-preview-img-${hash}`;
            previewContainer.innerHTML = `<img id="${uniqueId}" src="${gridEl.src}">`;
        } else if ((type === "audio" || type === "voice") && url) {
            const uniqueId = `edit-media-preview-audio-${hash}`;
            const dataUrl = gridEl.src;
            const mimeMatch = dataUrl.match(/^data:([^;,]+)/);
            const mimeType = mimeMatch ? mimeMatch[1] : "";
            if (window.GiftiaApp.isClientPlayableAudio(mimeType)) {
                previewContainer.innerHTML = `<audio id="${uniqueId}" src="${dataUrl}" controls></audio>`;
            } else {
                const friendly = mimeType ? mimeType.replace("audio/", "").toUpperCase() : "未知";
                previewContainer.innerHTML = `
                    <div class="media-audio-unsupported">
                        <div class="media-audio-unsupported-icon">🎧</div>
                        <div class="media-audio-unsupported-title">${friendly} 音频</div>
                        <div class="media-audio-unsupported-hint">PC 浏览器不支持此格式在线播放<br>（仅移动端 / IM WebView 可播放）</div>
                        <a href="#" class="btn btn-secondary btn-small media-audio-download-btn" onclick="window.downloadMedia('${hash}', '${mimeType}'); return false;">
                            📥 下载音频
                        </a>
                    </div>
                `;
            }
        } else {
            previewContainer.innerHTML = `<div style="font-size: 24px;">📄</div>`;
        }
    } else {
        if (type === "image" && url) {
            const uniqueId = `edit-media-preview-img-${hash}`;
            previewContainer.innerHTML = `<img id="${uniqueId}" src="placeholder.png" alt="加载中...">`;
            window.GiftiaApp.loadMediaFileB64(hash, uniqueId, url, type, false);
            
            if (gridEl && window.GiftiaApp.loadedOriginalMediaG) {
                if (!window.GiftiaApp.loadedOriginalMediaG.has(hash)) {
                    window.GiftiaApp.loadMediaFileB64(hash, gridElId, url, type, false);
                    window.GiftiaApp.loadedOriginalMediaG.add(hash);
                }
            }
        } else if ((type === "audio" || type === "voice") && url) {
            const uniqueId = `edit-media-preview-audio-${hash}`;
            previewContainer.innerHTML = `<audio id="${uniqueId}" controls></audio>`;
            window.GiftiaApp.loadMediaFileB64(hash, uniqueId, url, type);
        } else {
            previewContainer.innerHTML = `<div style="font-size: 24px;">📄</div>`;
        }
    }
    
    window.openModal("edit-media-modal");
};

window.submitEditMedia = async function() {
    const hash = document.getElementById("edit-media-hash").value;
    const caption = document.getElementById("edit-media-caption").value.trim();
    const genre = document.getElementById("edit-media-genre").value.trim();
    const character = document.getElementById("edit-media-character").value.trim();
    const source = document.getElementById("edit-media-source").value.trim();
    const text = document.getElementById("edit-media-text").value.trim();

    if (!caption) {
        window.showToast("描述内容不能为空！");
        return;
    }

    try {
        const res = await window.apiPost("/media/update", {
            hash_val: hash,
            caption: caption,
            genre: genre,
            character: character,
            source: source,
            text: text
        });
        if (res.status === "success") {
            window.showToast("更新成功！");
            window.closeModal("edit-media-modal");
            window.GiftiaApp.loadMedia();
        } else {
            window.showToast(`保存失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

// 5. Delete Media Caption
window.deleteMedia = function(hash) {
    window.showConfirm("确认清理媒体缓存", "确定要清理这条媒体缓存吗？这将清空它的大模型文字转述内容。", async () => {
        try {
            const res = await window.apiPost("/media/delete", { hash_val: hash });
            if (res.status === "success") {
                window.showToast("媒体描述已清理");
                window.GiftiaApp.loadMedia();
            } else {
                window.showToast(`清理失败: ${res.message}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

// 6. Fill energy
window.fillEnergy = async function(bot, group) {
    bot = decodeURIComponent(bot);
    group = decodeURIComponent(group);
    try {
        const res = await window.apiPost("/status/fill_energy", {
            bot_name: bot,
            group_or_user_id: group
        });
        if (res.status === "success") {
            window.showToast(`成功为 ${bot} 充满能量！`);
            window.GiftiaApp.loadBotStatus();
        } else {
            window.showToast(`补充能量失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

// 7. Edit Bot Status
window.openEditStatusModal = function(bot, group, mood, state, memory, action) {
    bot = decodeURIComponent(bot);
    group = decodeURIComponent(group);
    mood = decodeURIComponent(mood || "");
    state = decodeURIComponent(state || "");
    memory = decodeURIComponent(memory || "");
    action = decodeURIComponent(action || "");
    document.getElementById("edit-status-bot").value = bot;
    document.getElementById("edit-status-group").value = group;
    document.getElementById("edit-status-mood").value = mood;
    document.getElementById("edit-status-state").value = state;
    document.getElementById("edit-status-memory").value = memory;
    document.getElementById("edit-status-action").value = action;
    window.openModal("edit-status-modal");
};

window.submitEditStatus = async function() {
    const bot = document.getElementById("edit-status-bot").value;
    const group = document.getElementById("edit-status-group").value;
    const mood = document.getElementById("edit-status-mood").value.trim();
    const state = document.getElementById("edit-status-state").value.trim();
    const memory = document.getElementById("edit-status-memory").value.trim();
    const action = document.getElementById("edit-status-action").value.trim();

    try {
        const res = await window.apiPost("/status/update", {
            bot_name: bot,
            group_or_user_id: group,
            mood: mood,
            state: state,
            memory: memory,
            action: action
        });
        if (res.status === "success") {
            window.showToast("Bot 状态调整成功！");
            window.closeModal("edit-status-modal");
            window.GiftiaApp.loadBotStatus();
        } else {
            window.showToast(`修改失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

// 8. Short Task Board
const TASK_BOARD_STATUS_LABELS = {
    active: "活跃",
    completed: "完成",
    canceled: "取消",
    expired: "过期"
};

function formatTaskDateInput(value) {
    if (!value) return "";
    return String(value).replace(" ", "T").slice(0, 16);
}

window.taskBoardActiveTab = "active";
window.taskBoardCachedData = null;

window.setTaskBoardTab = function(tab) {
    window.taskBoardActiveTab = tab;
    document.querySelectorAll(".task-board-tab").forEach(btn => {
        if (btn.getAttribute("data-tab") === tab) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    if (window.taskBoardCachedData) {
        window.renderTaskListOnly(window.taskBoardCachedData);
    }
};

window.openTaskBoardModal = async function(bot, group) {
    bot = decodeURIComponent(bot);
    group = decodeURIComponent(group);
    document.getElementById("task-board-bot").value = bot;
    document.getElementById("task-board-group").value = group;
    document.getElementById("task-board-title").textContent = `短期任务 · ${bot}`;
    
    // Reset active tab to 'active' on open
    window.taskBoardActiveTab = "active";
    document.querySelectorAll(".task-board-tab").forEach(btn => {
        if (btn.getAttribute("data-tab") === "active") {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    document.getElementById("task-board-list").innerHTML = `<div class="loading-row flex-grow"><span class="loader"></span> 加载任务中...</div>`;
    window.openModal("task-board-modal");
    await window.refreshTaskBoardModal();
};

window.refreshTaskBoardModal = async function() {
    const bot = document.getElementById("task-board-bot").value;
    const group = document.getElementById("task-board-group").value;
    const list = document.getElementById("task-board-list");

    if (!bot || !group) {
        list.innerHTML = `<div class="task-board-empty">未选择会话</div>`;
        return;
    }

    try {
        const res = await window.apiGet("/task_board", {
            bot_name: bot,
            group_or_user_id: group
        });
        if (res.status !== "success" || !res.data) {
            throw new Error(res.message || "请求失败");
        }
        window.renderTaskBoardModal(res.data);
    } catch (e) {
        list.innerHTML = `<div class="task-board-empty">加载短期任务失败: ${window.escapeHtml(e.message)}</div>`;
    }
};

window.renderTaskBoardModal = function(data) {
    window.taskBoardCachedData = data;
    const stats = data.stats || {};
    const limit = data.limit || 0;

    const activeCount = stats.active || 0;
    const completedCount = stats.completed || 0;
    const archivedCount = (stats.canceled || 0) + (stats.expired || 0);
    const totalCount = stats.total || 0;

    const tabElements = document.querySelectorAll(".task-board-tab");
    tabElements.forEach(tab => {
        const tabType = tab.getAttribute("data-tab");
        let baseText = "";
        let svgHtml = "";
        if (tabType === "active") {
            baseText = "进行中";
            svgHtml = `<svg class="tab-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle></svg>`;
            tab.innerHTML = `${svgHtml} ${baseText} <span class="task-tab-count">${activeCount}/${limit}</span>`;
        } else if (tabType === "completed") {
            baseText = "已完成";
            svgHtml = `<svg class="tab-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
            tab.innerHTML = `${svgHtml} ${baseText} <span class="task-tab-count">${completedCount}</span>`;
        } else if (tabType === "archived") {
            baseText = "已失效";
            svgHtml = `<svg class="tab-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="9" x2="15" y2="9"></line><line x1="9" y1="13" x2="15" y2="13"></line><line x1="9" y1="17" x2="15" y2="17"></line></svg>`;
            tab.innerHTML = `${svgHtml} ${baseText} <span class="task-tab-count">${archivedCount}</span>`;
        } else if (tabType === "all") {
            baseText = "全部任务";
            svgHtml = `<svg class="tab-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>`;
            tab.innerHTML = `${svgHtml} ${baseText} <span class="task-tab-count">${totalCount}</span>`;
        }
    });

    window.renderTaskListOnly(data);
};

window.renderTaskListOnly = function(data) {
    const list = document.getElementById("task-board-list");
    const items = data.items || [];
    const activeTab = window.taskBoardActiveTab || "active";
    const statuses = ["active", "completed", "canceled", "expired"];

    let filteredItems = [];
    if (activeTab === "active") {
        filteredItems = items.filter(item => item.status === "active");
    } else if (activeTab === "completed") {
        filteredItems = items.filter(item => item.status === "completed");
    } else if (activeTab === "archived") {
        filteredItems = items.filter(item => item.status === "canceled" || item.status === "expired");
    } else {
        filteredItems = items;
    }

    if (filteredItems.length === 0) {
        let emptyTitle = "暂无任务";
        let emptyDesc = "当前分类下没有任何短期任务";
        if (activeTab === "active") {
            emptyTitle = "太棒了，暂无待办任务";
            emptyDesc = "当前没有任何进行中的短期任务，您可以让 Bot 自动帮您规划或在此记录新任务。";
        } else if (activeTab === "completed") {
            emptyTitle = "暂无已完成任务";
            emptyDesc = "已完成的短期任务记录将会归档在此处。";
        } else if (activeTab === "archived") {
            emptyTitle = "暂无失效任务";
            emptyDesc = "被取消或已过期的短期任务记录会归档在此处。";
        }
        
        list.innerHTML = `
            <div class="task-board-empty-state">
                <svg class="task-board-empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
                <div class="task-board-empty-title">${emptyTitle}</div>
                <div class="task-board-empty-desc">${emptyDesc}</div>
            </div>
        `;
        return;
    }

    let rowIndex = 0;
    list.innerHTML = filteredItems.map(task => {
        const originalIndex = data.items.findIndex(item => item.task_id === task.task_id);
        const index = originalIndex !== -1 ? originalIndex : rowIndex++;
        const taskIdArg = encodeURIComponent(task.task_id || "").replace(/'/g, "%27");
        const options = statuses.map(option => {
            const selected = option === task.status ? "selected" : "";
            return `<option value="${option}" ${selected}>${TASK_BOARD_STATUS_LABELS[option]}</option>`;
        }).join("");
        const creator = task.creator_nickname || task.creator_user_id || "未知";
        
        let badgeClass = "badge-secondary";
        if (task.status === "active") badgeClass = "badge-info";
        else if (task.status === "completed") badgeClass = "badge-success";
        else if (task.status === "canceled") badgeClass = "badge-danger";
        else if (task.status === "expired") badgeClass = "badge-warning";
        
        return `
            <div class="task-board-card">
                <div class="task-card-header">
                    <div class="task-card-status-badge">
                        <span class="badge ${badgeClass}">${TASK_BOARD_STATUS_LABELS[task.status] || task.status}</span>
                    </div>
                    <div class="task-card-meta-right">
                        <span>ID: ${window.escapeHtml(task.task_id || "")}</span>
                        <span>创建人: ${window.escapeHtml(creator)}</span>
                    </div>
                </div>
                <div class="task-card-body">
                    <textarea class="task-card-textarea" id="task-board-content-${index}" placeholder="任务内容...">${window.escapeHtml(task.content || "")}</textarea>
                </div>
                <div class="task-card-footer">
                    <div class="task-card-footer-left">
                        <div class="task-card-control-group">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"></path><path d="M12 6v6l4 2"></path></svg>
                            状态:
                            <select class="task-card-select" id="task-board-status-${index}">
                                ${options}
                            </select>
                        </div>
                        <div class="task-card-control-group">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                            截止:
                            <input class="task-card-input-date" id="task-board-expires-${index}" type="datetime-local" value="${window.escapeHtml(formatTaskDateInput(task.expires_at))}">
                        </div>
                        <div class="task-card-dates">
                            <span>创建: ${window.formatDate(task.created_at)}</span>
                            <span>更新: ${window.formatDate(task.updated_at)}</span>
                        </div>
                        ${task.close_reason ? `<div class="task-card-reason" title="${window.escapeHtml(task.close_reason)}">失效原因: ${window.escapeHtml(task.close_reason)}</div>` : ""}
                    </div>
                    <div class="task-card-actions">
                        <button class="task-card-btn task-card-btn-primary" onclick="window.saveTaskBoardItem(${index}, '${taskIdArg}')">
                            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
                            保存
                        </button>
                        <button class="task-card-btn task-card-btn-danger" onclick="window.deleteTaskBoardItem('${taskIdArg}')">
                            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                            删除
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join("");
};

window.saveTaskBoardItem = async function(index, taskIdEncoded) {
    const bot = document.getElementById("task-board-bot").value;
    const group = document.getElementById("task-board-group").value;
    const taskId = decodeURIComponent(taskIdEncoded);
    const content = document.getElementById(`task-board-content-${index}`).value.trim();
    const status = document.getElementById(`task-board-status-${index}`).value;
    const expiresAt = document.getElementById(`task-board-expires-${index}`).value;

    if (!content) {
        window.showToast("任务内容不能为空");
        return;
    }

    try {
        const res = await window.apiPost("/task_board/update", {
            bot_name: bot,
            group_or_user_id: group,
            task_id: taskId,
            content: content,
            status: status,
            expires_at: expiresAt
        });
        if (res.status === "success") {
            window.showToast("短期任务已更新");
            await window.refreshTaskBoardModal();
            window.GiftiaApp.loadBotStatus();
        } else {
            window.showToast(`更新失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

window.deleteTaskBoardItem = function(taskIdEncoded) {
    const bot = document.getElementById("task-board-bot").value;
    const group = document.getElementById("task-board-group").value;
    const taskId = decodeURIComponent(taskIdEncoded);

    window.showConfirm("确认删除短期任务", "确定要删除这条短期任务吗？此操作无法撤销。", async () => {
        try {
            const res = await window.apiPost("/task_board/delete", {
                bot_name: bot,
                group_or_user_id: group,
                task_id: taskId
            });
            if (res.status === "success") {
                window.showToast("短期任务已删除");
                await window.refreshTaskBoardModal();
                window.GiftiaApp.loadBotStatus();
            } else {
                window.showToast(`删除失败: ${res.message}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

// 9. Edit User Profile
window.openEditUserProfileModal = function(bot, group, user, profileEncoded, relation, titleEncoded, structuredEncoded) {
    const profile = decodeURIComponent(profileEncoded || "");
    const title = decodeURIComponent(titleEncoded || "");
    let structured = {};
    if (structuredEncoded) {
        try {
            structured = JSON.parse(decodeURIComponent(structuredEncoded));
        } catch (e) {
            structured = {};
        }
    }
    document.getElementById("edit-user-prof-bot").value = bot;
    document.getElementById("edit-user-prof-group").value = group;
    document.getElementById("edit-user-prof-user").value = user;
    document.getElementById("edit-user-prof-relation").value = relation !== undefined ? relation : 0;
    document.getElementById("edit-user-prof-title").value = title;
    document.getElementById("edit-user-prof-call-name").value = structured.call_name || "";
    document.getElementById("edit-user-prof-personality").value = structured.personality || "";
    document.getElementById("edit-user-prof-interests").value = structured.interests || "";
    document.getElementById("edit-user-prof-attitude").value = structured.attitude || "";
    document.getElementById("edit-user-prof-agreements").value = structured.agreements || "";
    document.getElementById("edit-user-prof-extra").value = structured.extra || "";
    document.getElementById("edit-user-prof-text").value = profile;
    window.openModal("edit-user-profile-modal");
};

window.submitEditUserProfile = async function() {
    const bot = document.getElementById("edit-user-prof-bot").value;
    const group = document.getElementById("edit-user-prof-group").value;
    const user = document.getElementById("edit-user-prof-user").value;
    const relationVal = document.getElementById("edit-user-prof-relation").value;
    const title = document.getElementById("edit-user-prof-title").value.trim();
    const profile = document.getElementById("edit-user-prof-text").value.trim();
    const callName = document.getElementById("edit-user-prof-call-name").value.trim();
    const personality = document.getElementById("edit-user-prof-personality").value.trim();
    const interests = document.getElementById("edit-user-prof-interests").value.trim();
    const attitude = document.getElementById("edit-user-prof-attitude").value.trim();
    const agreements = document.getElementById("edit-user-prof-agreements").value.trim();
    const extra = document.getElementById("edit-user-prof-extra").value.trim();

    const relation = relationVal !== "" ? parseInt(relationVal) : 0;

    try {
        const res = await window.apiPost("/profiles/user/update", {
            bot_name: bot,
            group_or_user_id: group,
            user_id: user,
            profile: profile,
            relation: relation,
            title: title,
            call_name: callName,
            personality: personality,
            interests: interests,
            attitude: attitude,
            agreements: agreements,
            extra: extra
        });
        if (res.status === "success") {
            window.showToast("保存成功！");
            window.closeModal("edit-user-profile-modal");
            window.GiftiaApp.loadUserProfiles();
        } else {
            window.showToast(`更新失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

function getUserAliasScope() {
    return {
        bot_name: document.getElementById("user-alias-bot").value,
        group_or_user_id: document.getElementById("user-alias-group").value,
        user_id: document.getElementById("user-alias-user").value
    };
}

window.openUserAliasesModal = async function(bot, group, user) {
    document.getElementById("user-alias-bot").value = bot;
    document.getElementById("user-alias-group").value = group;
    document.getElementById("user-alias-user").value = user;
    document.getElementById("user-alias-user-display").value = user;
    document.getElementById("user-alias-new").value = "";
    window.openModal("user-aliases-modal");
    await window.loadUserAliases();
};

window.loadUserAliases = async function() {
    const scope = getUserAliasScope();
    const list = document.getElementById("user-alias-list");
    list.innerHTML = `<tr><td colspan="5" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;
    try {
        const res = await window.apiGet("/profiles/user/aliases", scope);
        if (res.status !== "success") {
            throw new Error(res.message || "请求失败");
        }
        const items = (res.data && res.data.items) || [];
        if (items.length === 0) {
            list.innerHTML = `<tr><td colspan="5" class="no-data-row">暂无外号记录</td></tr>`;
            return;
        }
        list.innerHTML = items.map(item => {
            const alias = item.alias || "";
            const encodedAlias = encodeURIComponent(alias);
            const aliasCount = Math.max(1, parseInt(item.alias_count) || 1);
            return `
                <tr>
                    <td data-label="外号">${window.escapeHtml(alias)}</td>
                    <td data-label="次数">
                        <input type="number" class="alias-count-input" min="1" step="1" value="${aliasCount}">
                    </td>
                    <td data-label="首次出现">${window.formatDate(item.first_seen_at)}</td>
                    <td data-label="最近出现">${window.formatDate(item.last_seen_at)}</td>
                    <td data-label="操作" class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="window.saveUserAliasCount('${encodedAlias}', this)">保存次数</button>
                        <button class="btn btn-danger btn-small" onclick="window.deleteUserAlias('${encodedAlias}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    } catch (e) {
        list.innerHTML = `<tr><td colspan="5" class="no-data-row">加载数据失败: ${window.escapeHtml(e.message)}</td></tr>`;
    }
};

window.submitAddUserAlias = async function() {
    const aliasInput = document.getElementById("user-alias-new");
    const alias = aliasInput.value.trim();
    if (!alias) {
        window.showToast("外号不能为空");
        return;
    }
    try {
        const res = await window.apiPost("/profiles/user/aliases/add", {
            ...getUserAliasScope(),
            alias: alias
        });
        if (res.status === "success") {
            aliasInput.value = "";
            window.showToast("外号已新增");
            await window.loadUserAliases();
            window.GiftiaApp.loadUserProfiles();
        } else {
            window.showToast(`新增失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

window.saveUserAliasCount = async function(aliasEncoded, button) {
    const alias = decodeURIComponent(aliasEncoded || "");
    const input = button.closest("tr").querySelector(".alias-count-input");
    const aliasCount = Number(input.value);
    if (!Number.isInteger(aliasCount) || aliasCount < 1) {
        window.showToast("统计次数必须是正整数");
        return;
    }
    try {
        const res = await window.apiPost("/profiles/user/aliases/count", {
            ...getUserAliasScope(),
            alias: alias,
            alias_count: aliasCount
        });
        if (res.status === "success") {
            window.showToast("统计次数已保存");
            await window.loadUserAliases();
            window.GiftiaApp.loadUserProfiles();
        } else {
            window.showToast(`保存失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

window.deleteUserAlias = function(aliasEncoded) {
    const alias = decodeURIComponent(aliasEncoded || "");
    window.showConfirm("确认删除外号", `确定要删除外号「${alias}」吗？`, async () => {
        try {
            const res = await window.apiPost("/profiles/user/aliases/delete", {
                ...getUserAliasScope(),
                alias: alias
            });
            if (res.status === "success") {
                window.showToast("外号已删除");
                await window.loadUserAliases();
                window.GiftiaApp.loadUserProfiles();
            } else {
                window.showToast(`删除失败: ${res.message}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

window.deleteUserProfile = function(bot, group, user) {
    window.showConfirm("确认删除用户画像", "确定要删除该用户的画像总结吗？此操作不可逆。", async () => {
        try {
            const res = await window.apiPost("/profiles/user/delete", {
                bot_name: bot,
                group_or_user_id: group,
                user_id: user
            });
            if (res.status === "success") {
                window.showToast("删除画像成功");
                window.GiftiaApp.loadUserProfiles();
            } else {
                window.showToast(`删除失败: ${res.message}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

// 10. Edit Group Profile
window.openEditGroupProfileModal = function(bot, group, profileEncoded) {
    const profile = decodeURIComponent(profileEncoded);
    document.getElementById("edit-group-prof-bot").value = bot;
    document.getElementById("edit-group-prof-group").value = group;
    document.getElementById("edit-group-prof-text").value = profile;
    window.openModal("edit-group-profile-modal");
};

window.submitEditGroupProfile = async function() {
    const bot = document.getElementById("edit-group-prof-bot").value;
    const group = document.getElementById("edit-group-prof-group").value;
    const profile = document.getElementById("edit-group-prof-text").value.trim();

    if (!profile) {
        window.showToast("画像内容不能为空！");
        return;
    }

    try {
        const res = await window.apiPost("/profiles/group/update", {
            bot_name: bot,
            group_or_user_id: group,
            profile: profile
        });
        if (res.status === "success") {
            window.showToast("保存成功！");
            window.closeModal("edit-group-profile-modal");
            window.GiftiaApp.loadGroupProfiles();
        } else {
            window.showToast(`更新失败: ${res.message}`);
        }
    } catch (e) {
        window.showToast(`发生错误: ${e.message}`);
    }
};

window.deleteGroupProfile = function(bot, group) {
    window.showConfirm("确认删除群聊画像", "确定要删除该群聊的画像总结吗？此操作不可逆。", async () => {
        try {
            const res = await window.apiPost("/profiles/group/delete", {
                bot_name: bot,
                group_or_user_id: group
            });
            if (res.status === "success") {
                window.showToast("删除画像成功");
                window.GiftiaApp.loadGroupProfiles();
            } else {
                window.showToast(`删除失败: ${res.message}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

// 11. Cache Cleanup
window.openCleanCacheModal = async function() {
    const container = document.getElementById("clean-media-genre-container");
    container.innerHTML = '<div style="font-size: 12px; color: var(--font-secondary);">加载中...</div>';
    
    document.getElementById("clean-media-type").value = "all";
    document.getElementById("clean-max-query-times").value = "0";
    document.getElementById("clean-genre-exclude").checked = false;
    document.getElementById("clean-cache-preview-info").innerHTML = '点击下方“计算清理空间”进行预估...';
    document.getElementById("clean-cache-preview-info").style.borderLeftColor = "var(--border-color)";

    try {
        const res = await window.apiGet("/media/genres");
        if (res && res.status === "success" && res.genres) {
            container.innerHTML = "";
            
            const unspecifiedDiv = document.createElement("div");
            unspecifiedDiv.style.display = "flex";
            unspecifiedDiv.style.alignItems = "center";
            unspecifiedDiv.style.gap = "6px";
            unspecifiedDiv.style.margin = "4px 0";
            unspecifiedDiv.innerHTML = `
                <input type="checkbox" id="clean-genre-unspecified" value="" style="width: auto; margin: 0; cursor: pointer;" checked>
                <label for="clean-genre-unspecified" style="margin: 0; cursor: pointer; font-weight: normal; color: var(--font-primary);">[未指定风格]</label>
            `;
            container.appendChild(unspecifiedDiv);

            res.genres.forEach((genre, idx) => {
                const genreDiv = document.createElement("div");
                genreDiv.style.display = "flex";
                genreDiv.style.alignItems = "center";
                genreDiv.style.gap = "6px";
                genreDiv.style.margin = "4px 0";
                genreDiv.innerHTML = `
                    <input type="checkbox" name="clean-genre-checkbox" id="clean-genre-chk-${idx}" value="${window.escapeHtml(genre)}" style="width: auto; margin: 0; cursor: pointer;" checked>
                    <label for="clean-genre-chk-${idx}" style="margin: 0; cursor: pointer; font-weight: normal; color: var(--font-primary);">${window.escapeHtml(genre)}</label>
                `;
                container.appendChild(genreDiv);
            });
        } else {
            container.innerHTML = '<div style="font-size: 12px; color: var(--font-secondary);">暂无可用风格，或加载失败。</div>';
        }
    } catch (e) {
        console.error("Failed to load genres for cleanup modal:", e);
        container.innerHTML = '<div style="font-size: 12px; color: var(--font-secondary);">加载风格列表出错。</div>';
    }

    // Reset clean modal tabs to default (manual tab)
    const cleanModal = document.getElementById("clean-cache-modal");
    if (cleanModal) {
        cleanModal.querySelectorAll(".media-tab-btn").forEach(b => {
            if (b.getAttribute("data-mediatab") === "clean-manual") {
                b.classList.add("active");
            } else {
                b.classList.remove("active");
            }
        });
        cleanModal.querySelectorAll(".media-tab-panel").forEach(p => {
            if (p.id === "mediatab-clean-manual") {
                p.classList.add("active");
            } else {
                p.classList.remove("active");
            }
        });
        const btnManualCalc = cleanModal.querySelector("#btn-manual-calc");
        const btnManualSubmit = cleanModal.querySelector("#btn-manual-submit");
        const btnAutoTrigger = cleanModal.querySelector("#btn-auto-trigger");
        const btnAutoSave = cleanModal.querySelector("#btn-auto-save");
        if (btnManualCalc) btnManualCalc.style.display = "inline-block";
        if (btnManualSubmit) btnManualSubmit.style.display = "inline-block";
        if (btnAutoTrigger) btnAutoTrigger.style.display = "none";
        if (btnAutoSave) btnAutoSave.style.display = "none";
    }

    window.openModal("clean-cache-modal");
};

window.toggleAllCleanGenres = function(checked) {
    const unspecified = document.getElementById("clean-genre-unspecified");
    if (unspecified) unspecified.checked = checked;
    
    const checkboxes = document.getElementsByName("clean-genre-checkbox");
    checkboxes.forEach(chk => chk.checked = checked);
};

window.invertCleanGenres = function() {
    const unspecified = document.getElementById("clean-genre-unspecified");
    if (unspecified) unspecified.checked = !unspecified.checked;
    
    const checkboxes = document.getElementsByName("clean-genre-checkbox");
    checkboxes.forEach(chk => chk.checked = !chk.checked);
};

function getSelectedCleanGenres() {
    const selected = [];
    const unspecified = document.getElementById("clean-genre-unspecified");
    if (unspecified && unspecified.checked) {
        selected.push("");
    }
    
    const checkboxes = document.getElementsByName("clean-genre-checkbox");
    checkboxes.forEach(chk => {
        if (chk.checked) {
            selected.push(chk.value);
        }
    });
    return selected;
}

window.calculateCleanSpace = async function() {
    const mediaType = document.getElementById("clean-media-type").value;
    const genres = getSelectedCleanGenres();
    const excludeGenres = document.getElementById("clean-genre-exclude").checked;
    const maxQueryTimesVal = document.getElementById("clean-max-query-times").value.trim();
    const maxQueryTimes = maxQueryTimesVal !== "" ? parseInt(maxQueryTimesVal, 10) : null;

    const infoBox = document.getElementById("clean-cache-preview-info");
    infoBox.innerHTML = '正在计算，请稍候...';
    infoBox.style.borderLeftColor = "var(--primary-color)";

    try {
        const res = await window.apiPost("/media/cache/clean", {
            media_type: mediaType,
            genres: genres,
            exclude_genres: excludeGenres,
            max_query_times: maxQueryTimes,
            dry_run: true
        });
        if (res && res.status === "success") {
            const formattedSize = window.formatBytes(res.size_bytes);
            infoBox.innerHTML = `<strong>预估结果：</strong><br>匹配的缓存文件数: <strong>${res.count}</strong> 个<br>预计可释放空间: <strong>${formattedSize}</strong>`;
            infoBox.style.borderLeftColor = "var(--success-color, #4caf50)";
        } else {
            infoBox.innerHTML = `计算失败: ${res.message || "请求出错"}`;
            infoBox.style.borderLeftColor = "var(--danger-color, #f44336)";
        }
    } catch (e) {
        infoBox.innerHTML = `计算出错: ${e.message}`;
        infoBox.style.borderLeftColor = "var(--danger-color, #f44336)";
    }
};

window.submitCleanCache = async function() {
    const mediaType = document.getElementById("clean-media-type").value;
    const genres = getSelectedCleanGenres();
    const excludeGenres = document.getElementById("clean-genre-exclude").checked;
    const maxQueryTimesVal = document.getElementById("clean-max-query-times").value.trim();
    const maxQueryTimes = maxQueryTimesVal !== "" ? parseInt(maxQueryTimesVal, 10) : null;

    window.showConfirm("确认清理缓存", "确定要清理符合条件的媒体文件缓存吗？此操作将物理删除本地缓存文件（保留转述文字描述），不可逆。", async () => {
        try {
            const res = await window.apiPost("/media/cache/clean", {
                media_type: mediaType,
                genres: genres,
                exclude_genres: excludeGenres,
                max_query_times: maxQueryTimes,
                dry_run: false
            });
            if (res && res.status === "success") {
                const formattedSize = window.formatBytes(res.size_bytes);
                window.showToast(`清理成功！共清理 ${res.count} 个文件，释放空间 ${formattedSize}`);
                window.closeModal("clean-cache-modal");
                window.GiftiaApp.pagination.media.page = 1;
                window.GiftiaApp.loadMedia();
            } else {
                window.showToast(`清理失败: ${res.message || "请求出错"}`);
            }
        } catch (e) {
            window.showToast(`发生错误: ${e.message}`);
        }
    });
};

window.loadAutoCleanConfig = async function() {
    const container = document.getElementById("auto-clean-genre-container");
    container.innerHTML = '<div style="font-size: 12px; color: var(--font-secondary);">加载中...</div>';
    
    try {
        // Fetch config and distinct genres
        const configRes = await window.apiGet("/media/cache/auto_clean/config");
        const genresRes = await window.apiGet("/media/genres");
        
        if (configRes && configRes.status === "success" && genresRes && genresRes.status === "success") {
            const config = configRes.config || { enabled: false, keep_genres: ["表情包", "sticker"] };
            const enabledCheckbox = document.getElementById("auto-clean-enabled");
            if (enabledCheckbox) {
                enabledCheckbox.checked = config.enabled;
            }
            
            container.innerHTML = "";
            
            // Add unspecified genre checkbox
            const unspecifiedDiv = document.createElement("div");
            unspecifiedDiv.style.display = "flex";
            unspecifiedDiv.style.alignItems = "center";
            unspecifiedDiv.style.gap = "6px";
            unspecifiedDiv.style.margin = "4px 0";
            const isUnspecifiedChecked = config.keep_genres.includes("");
            unspecifiedDiv.innerHTML = `
                <input type="checkbox" id="auto-clean-genre-unspecified" value="" style="width: auto; margin: 0; cursor: pointer;" ${isUnspecifiedChecked ? "checked" : ""}>
                <label for="auto-clean-genre-unspecified" style="margin: 0; cursor: pointer; font-weight: normal; color: var(--font-primary);">[未指定风格]</label>
            `;
            container.appendChild(unspecifiedDiv);
            
            // Add other genres
            genresRes.genres.forEach((genre, idx) => {
                const genreDiv = document.createElement("div");
                genreDiv.style.display = "flex";
                genreDiv.style.alignItems = "center";
                genreDiv.style.gap = "6px";
                genreDiv.style.margin = "4px 0";
                const isChecked = config.keep_genres.includes(genre);
                genreDiv.innerHTML = `
                    <input type="checkbox" name="auto-clean-genre-checkbox" id="auto-clean-genre-chk-${idx}" value="${window.escapeHtml(genre)}" style="width: auto; margin: 0; cursor: pointer;" ${isChecked ? "checked" : ""}>
                    <label for="auto-clean-genre-chk-${idx}" style="margin: 0; cursor: pointer; font-weight: normal; color: var(--font-primary);">${window.escapeHtml(genre)}</label>
                `;
                container.appendChild(genreDiv);
            });
        } else {
            container.innerHTML = '<div style="font-size: 12px; color: var(--font-secondary);">加载配置失败。</div>';
        }
    } catch (e) {
        console.error("Failed to load auto-clean config:", e);
        container.innerHTML = '<div style="font-size: 12px; color: var(--font-secondary);">加载配置出错。</div>';
    }
};

window.saveAutoCleanConfig = async function() {
    const enabledCheckbox = document.getElementById("auto-clean-enabled");
    const enabled = enabledCheckbox ? enabledCheckbox.checked : false;
    
    const keep_genres = [];
    const unspecifiedChk = document.getElementById("auto-clean-genre-unspecified");
    if (unspecifiedChk && unspecifiedChk.checked) {
        keep_genres.push("");
    }
    
    document.querySelectorAll('input[name="auto-clean-genre-checkbox"]').forEach(chk => {
        if (chk.checked) {
            keep_genres.push(chk.value);
        }
    });
    
    try {
        const res = await window.apiPost("/media/cache/auto_clean/config", {
            enabled: enabled,
            keep_genres: keep_genres
        });
        
        if (res && res.status === "success") {
            window.showToast("自动清理配置保存成功！");
        } else {
            window.showToast(`保存失败: ${res.message || "未知错误"}`);
        }
    } catch (e) {
        console.error("Failed to save auto-clean config:", e);
        window.showToast("保存配置出错");
    }
};

window.triggerAutoCleanImmediately = async function() {
    window.showConfirm("确认执行自动清理", "确认要立即运行一次自动清理吗？这将按照当前设定的规则，物理删除过期超出会话窗口且不属于保留范围的媒体缓存文件，不可逆。", async () => {
        try {
            const res = await window.apiPost("/media/cache/auto_clean/trigger", {});
            if (res && res.status === "success") {
                const formattedSize = window.formatBytes(res.size_bytes);
                window.showToast(`清理成功！共释放空间 ${formattedSize}，物理删除 ${res.count} 个文件`);
                window.closeModal("clean-cache-modal");
                window.GiftiaApp.pagination.media.page = 1;
                window.GiftiaApp.loadMedia();
            } else {
                window.showToast(`执行清理失败: ${res.message || "请求出错"}`);
            }
        } catch (e) {
            console.error("Failed to trigger auto-clean:", e);
            window.showToast("触发自动清理出错");
        }
    });
};

window.clearChatHistory = async function() {
    const botName = document.getElementById("history-bot-name").value;
    const groupOrUserId = document.getElementById("history-group-id").value;
    if (!botName || !groupOrUserId) {
        window.showToast("当前没有选中的会话");
        return;
    }

    window.showConfirm("确认清空会话消息", `确定要清空 Bot [${botName}] 在会话 [${groupOrUserId}] 中的所有决策审计消息吗？此操作无法撤销。`, async () => {
        try {
            const res = await window.apiPost("/chat_history/delete", {
                bot_name: botName,
                group_or_user_id: groupOrUserId
            });
            if (res && res.status === "success") {
                window.showToast("会话消息清空成功");
                // Reset page to 1
                window.GiftiaApp.resetPagination("history");
                // Refresh filters and reload data
                await window.GiftiaApp.initializeScopedView("history");
            } else {
                window.showToast(`清空失败: ${res.message || "未知错误"}`);
            }
        } catch (e) {
            console.error("Failed to clear chat history:", e);
            window.showToast("清空会话消息出错");
        }
    });
};
