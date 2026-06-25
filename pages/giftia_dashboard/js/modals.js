// Giftia Dashboard Modal Actions

window.openModal = function(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add("show");
    }
};

window.closeModal = function(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove("show");
    }
};

// 1. Add Memory
window.openAddMemoryModal = function() {
    document.getElementById("add-mem-bot").value = "";
    document.getElementById("add-mem-group").value = "";
    document.getElementById("add-mem-associated-ids").value = "";
    document.getElementById("add-mem-text").value = "";
    window.openModal("add-memory-modal");
};

window.submitAddMemory = async function() {
    const botName = document.getElementById("add-mem-bot").value.trim();
    const groupId = document.getElementById("add-mem-group").value.trim();
    const associatedIds = document.getElementById("add-mem-associated-ids").value.trim();
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
            associated_user_ids: associatedIds
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
window.openEditMemoryModal = function(id, bot, group, textEncoded, associatedIds) {
    const text = decodeURIComponent(textEncoded);
    document.getElementById("edit-mem-id").value = id;
    document.getElementById("edit-mem-bot").value = bot;
    document.getElementById("edit-mem-group").value = group;
    document.getElementById("edit-mem-associated-ids").value = associatedIds || "";
    document.getElementById("edit-mem-text").value = text;
    window.openModal("edit-memory-modal");
};

window.submitEditMemory = async function() {
    const id = document.getElementById("edit-mem-id").value;
    const bot = document.getElementById("edit-mem-bot").value;
    const group = document.getElementById("edit-mem-group").value;
    const associatedIds = document.getElementById("edit-mem-associated-ids").value.trim();
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
            associated_user_ids: associatedIds
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
            previewContainer.innerHTML = `<audio id="${uniqueId}" src="${gridEl.src}" controls></audio>`;
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

// 8. Edit User Profile
window.openEditUserProfileModal = function(bot, group, user, profileEncoded, relation, titleEncoded) {
    const profile = decodeURIComponent(profileEncoded);
    const title = decodeURIComponent(titleEncoded || "");
    document.getElementById("edit-user-prof-bot").value = bot;
    document.getElementById("edit-user-prof-group").value = group;
    document.getElementById("edit-user-prof-user").value = user;
    document.getElementById("edit-user-prof-relation").value = relation !== undefined ? relation : 0;
    document.getElementById("edit-user-prof-title").value = title;
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

    if (!profile) {
        window.showToast("画像内容不能为空！");
        return;
    }

    const relation = relationVal !== "" ? parseInt(relationVal) : 0;

    try {
        const res = await window.apiPost("/profiles/user/update", {
            bot_name: bot,
            group_or_user_id: group,
            user_id: user,
            profile: profile,
            relation: relation,
            title: title
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

// 9. Edit Group Profile
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

// 10. Cache Cleanup
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
