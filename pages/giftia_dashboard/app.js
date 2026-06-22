// Giftia Dashboard Frontend Logic

document.addEventListener("DOMContentLoaded", () => {
    // Current state variables
    let activeTab = "chat-history";
    let activeSubTab = "user-profiles";
    
    // Pagination states
    const pagination = {
        history: { page: 1, limit: 15, total: 0 },
        memories: { page: 1, limit: 15, total: 0 },
        media: { page: 1, limit: 12, total: 0 },
        userProfiles: { page: 1, limit: 15, total: 0 },
        groupProfiles: { page: 1, limit: 15, total: 0 }
    };

    // Initialize AstrBot Bridge SDK
    if (window.AstrBotPluginPage) {
        window.AstrBotPluginPage.ready().then((context) => {
            console.log("AstrBot Plugin Bridge Ready. Context:", context);
            // Apply initial theme
            if (context && typeof context.isDark === "boolean") {
                document.documentElement.setAttribute(
                    "data-theme",
                    context.isDark ? "dark" : "light"
                );
            }
            
            // Initial data load
            loadActiveTabData();
        }).catch((err) => {
            console.error("Failed to initialize AstrBot Bridge SDK:", err);
            // Fallback load anyway
            loadActiveTabData();
        });
        
        // Listen to parent theme changes
        window.AstrBotPluginPage.onContext((ctx) => {
            if (ctx && typeof ctx.isDark === "boolean") {
                document.documentElement.setAttribute(
                    "data-theme",
                    ctx.isDark ? "dark" : "light"
                );
            }
        });
    } else {
        // Fallback for standalone preview
        loadActiveTabData();
    }

    // Tab Navigation setup
    const tabButtons = document.querySelectorAll(".nav-tab");
    const tabPanels = document.querySelectorAll(".tab-panel");

    tabButtons.forEach(button => {
        button.addEventListener("click", () => {
            const targetTab = button.getAttribute("data-tab");
            
            tabButtons.forEach(btn => btn.classList.remove("active"));
            tabPanels.forEach(panel => panel.classList.remove("active"));
            
            button.classList.add("active");
            const targetPanel = document.getElementById(`tab-${targetTab}`);
            if (targetPanel) {
                targetPanel.classList.add("active");
            }
            
            activeTab = targetTab;
            loadActiveTabData();
        });
    });

    // Sub-tab Navigation setup (Profiles)
    const subTabButtons = document.querySelectorAll(".sub-nav-tab");
    const subPanels = document.querySelectorAll(".subpanel");

    subTabButtons.forEach(button => {
        button.addEventListener("click", () => {
            const targetSubTab = button.getAttribute("data-subtab");
            
            subTabButtons.forEach(btn => btn.classList.remove("active"));
            subPanels.forEach(panel => panel.classList.remove("active"));
            
            button.classList.add("active");
            const targetSubPanel = document.getElementById(`subpanel-${targetSubTab}`);
            if (targetSubPanel) {
                targetSubPanel.classList.add("active");
            }
            
            activeSubTab = targetSubTab;
            loadProfilesData();
        });
    });

    // Helper: Load data based on active tab
    function loadActiveTabData() {
        if (activeTab === "chat-history") {
            loadChatHistory();
        } else if (activeTab === "memories") {
            loadMemories();
        } else if (activeTab === "bot-status") {
            loadBotStatus();
        } else if (activeTab === "media-captions") {
            loadMedia();
        } else if (activeTab === "profiles") {
            loadProfilesData();
        }
    }

    // Helper: Make API calls with fallback
    async function apiGet(endpoint, params) {
        if (window.AstrBotPluginPage) {
            try {
                const res = await window.AstrBotPluginPage.apiGet(endpoint, params);
                if (res && typeof res === "object" && "status" in res) {
                    return res;
                }
                return { status: "success", data: res };
            } catch (e) {
                return { status: "error", message: e.message };
            }
        }
        return fetch(`${endpoint}?${new URLSearchParams(params)}`).then(r => r.json());
    }

    async function apiPost(endpoint, body) {
        if (window.AstrBotPluginPage) {
            try {
                const res = await window.AstrBotPluginPage.apiPost(endpoint, body);
                if (res && typeof res === "object" && "status" in res) {
                    return res;
                }
                return { status: "success", data: res };
            } catch (e) {
                return { status: "error", message: e.message };
            }
        }
        return fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        }).then(r => r.json());
    }


    // Filter Listeners (Debounced)
    let filterTimeout;
    const filterInputIds = [
        "history-bot-name", "history-group-id", "history-decision", "history-rag", "history-search",
        "memory-bot-name", "memory-group-id", "memory-search",
        "media-type", "media-search",
        "user-profile-bot-name", "user-profile-group-id", "user-profile-user-id", "user-profile-search",
        "group-profile-bot-name", "group-profile-group-id", "group-profile-search"
    ];

    filterInputIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            const eventType = el.tagName === "SELECT" ? "change" : "input";
            el.addEventListener(eventType, () => {
                clearTimeout(filterTimeout);
                filterTimeout = setTimeout(() => {
                    // Reset pagination page and reload
                    if (activeTab === "chat-history") {
                        pagination.history.page = 1;
                        loadChatHistory();
                    } else if (activeTab === "memories") {
                        pagination.memories.page = 1;
                        loadMemories();
                    } else if (activeTab === "media-captions") {
                        pagination.media.page = 1;
                        loadMedia();
                    } else if (activeTab === "profiles") {
                        if (activeSubTab === "user-profiles") {
                            pagination.userProfiles.page = 1;
                            loadUserProfiles();
                        } else {
                            pagination.groupProfiles.page = 1;
                            loadGroupProfiles();
                        }
                    }
                }, 300);
            });
        }
    });

    // ----------------------------------------------------
    // TAB 1: Chat History
    // ----------------------------------------------------
    async function loadChatHistory() {
        const listContainer = document.getElementById("history-list");
        listContainer.innerHTML = `<tr><td colspan="6" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: pagination.history.page,
            limit: pagination.history.limit,
            bot_name: document.getElementById("history-bot-name").value,
            group_or_user_id: document.getElementById("history-group-id").value,
            reply_decision: document.getElementById("history-decision").value,
            use_rag: document.getElementById("history-rag").value,
            search: document.getElementById("history-search").value
        };

        try {
            const res = await apiGet("/chat_history", params);
            if (res.status === "success" && res.data) {
                pagination.history.total = res.data.total;
                renderChatHistory(res.data.items);
                renderPagination("history-pagination", pagination.history, loadChatHistoryPage);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="6" class="no-data-row">⚠️ 加载数据失败: ${e.message}</td></tr>`;
        }
    }

    function loadChatHistoryPage(page) {
        pagination.history.page = page;
        loadChatHistory();
    }

    function renderChatHistory(items) {
        const container = document.getElementById("history-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="6" class="no-data-row">暂无相关聊天记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            let decisionBadge = "";
            if (item.reply_decision === 1) {
                decisionBadge = `<span class="badge badge-success">通过 (已回复)</span>`;
            } else if (item.reply_decision === 0) {
                decisionBadge = `<span class="badge badge-danger">忽略</span>`;
            } else if (item.reply_decision === 3) {
                decisionBadge = `<span class="badge badge-info">唤醒直接回复</span>`;
            } else {
                decisionBadge = `<span class="badge badge-secondary">未审查</span>`;
            }

            const ragBadge = item.use_rag === 1
                ? `<span class="badge badge-info">触发 RAG</span>`
                : `<span class="badge badge-secondary">未触发</span>`;

            // Display nickname or sender_id nicely
            const senderDisp = item.nickname ? `${item.nickname} (${item.user_id})` : item.user_id;

            return `
                <tr>
                    <td style="white-space: nowrap;">${formatDate(item.created_at)}</td>
                    <td style="font-weight: 600;">${item.bot_name}</td>
                    <td>
                        <div style="font-size: 13px;">${senderDisp}</div>
                        <div style="font-size: 11px; color: var(--font-secondary);">群组: ${item.group_or_user_id}</div>
                    </td>
                    <td>
                        <div style="max-width: 480px; word-break: break-all;">${escapeHtml(item.content)}</div>
                    </td>
                    <td>${decisionBadge}</td>
                    <td>${ragBadge}</td>
                </tr>
            `;
        }).join("");
    }

    // ----------------------------------------------------
    // TAB 2: Memories
    // ----------------------------------------------------
    async function loadMemories() {
        const listContainer = document.getElementById("memory-list");
        listContainer.innerHTML = `<tr><td colspan="5" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: pagination.memories.page,
            limit: pagination.memories.limit,
            bot_name: document.getElementById("memory-bot-name").value,
            group_or_user_id: document.getElementById("memory-group-id").value,
            search: document.getElementById("memory-search").value
        };

        try {
            const res = await apiGet("/memories", params);
            if (res.status === "success" && res.data) {
                pagination.memories.total = res.data.total;
                renderMemories(res.data.items);
                renderPagination("memory-pagination", pagination.memories, loadMemoriesPage);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="5" class="no-data-row">⚠️ 加载数据失败: ${e.message}</td></tr>`;
        }
    }

    function loadMemoriesPage(page) {
        pagination.memories.page = page;
        loadMemories();
    }

    function renderMemories(items) {
        const container = document.getElementById("memory-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="5" class="no-data-row">暂无相关长期记忆记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedText = encodeURIComponent(item.text);
            return `
                <tr>
                    <td style="font-weight: 600;">${item.bot_name}</td>
                    <td>${item.group_or_user_id}</td>
                    <td>
                        <div style="max-width: 550px; word-break: break-all;">${escapeHtml(item.text)}</div>
                    </td>
                    <td>${formatDate(item.created_at)}</td>
                    <td class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="openEditMemoryModal('${item.memory_id}', '${item.bot_name}', '${item.group_or_user_id}', '${encodedText}')">编辑</button>
                        <button class="btn btn-danger btn-small" onclick="deleteMemory('${item.memory_id}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    }

    // ----------------------------------------------------
    // TAB 3: Bot Status
    // ----------------------------------------------------
    async function loadBotStatus() {
        const container = document.getElementById("status-grid");
        container.innerHTML = `<div class="loading-row flex-grow"><span class="loader"></span> 加载状态中...</div>`;

        try {
            const res = await apiGet("/status");
            if (res.status === "success" && res.data) {
                renderBotStatus(res.data);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            container.innerHTML = `<div class="no-data-row flex-grow">⚠️ 加载状态失败: ${e.message}</div>`;
        }
    }

    function renderBotStatus(items) {
        const container = document.getElementById("status-grid");
        if (!items || items.length === 0) {
            container.innerHTML = `<div class="no-data-row flex-grow">目前暂无活动的会话状态，请让机器人先和群友聊天试试吧！</div>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const energy = parseFloat(item.energy) || 0;
            const energyClass = energy < 20 ? "low-energy" : "";
            const mood = item.mood || "平静";
            const state = item.state || "发呆";
            
            return `
                <div class="status-card card">
                    <div class="status-header">
                        <div class="status-bot-title">
                            <h3>${item.bot_name}</h3>
                            <p>会话: ${item.group_or_user_id}</p>
                        </div>
                        <span class="badge badge-info">${state}</span>
                    </div>
                    <div class="status-body">
                        <div class="status-row">
                            <span class="status-label">心情</span>
                            <span class="status-value">${mood}</span>
                        </div>
                        <div class="status-row">
                            <span class="status-label">最新状态</span>
                            <span class="status-value">${item.action || "无"}</span>
                        </div>
                        <div class="status-row" style="flex-direction: column; gap: 4px;">
                            <div style="display: flex; justify-content: space-between;">
                                <span class="status-label">能量</span>
                                <span class="status-value ${energyClass}">${energy.toFixed(1)}%</span>
                            </div>
                            <div class="energy-bar-container">
                                <div class="energy-bar-fill ${energyClass}" style="width: ${energy}%"></div>
                            </div>
                        </div>
                    </div>
                    <div class="status-actions">
                        <button class="btn btn-secondary btn-small" onclick="openEditStatusModal('${item.bot_name}', '${item.group_or_user_id}', '${mood}', '${state}')">调整状态</button>
                        <button class="btn btn-primary btn-small" onclick="fillEnergy('${item.bot_name}', '${item.group_or_user_id}')">⚡ 满能</button>
                    </div>
                </div>
            `;
        }).join("");
    }

    // ----------------------------------------------------
    // TAB 4: Media Captions
    // ----------------------------------------------------
    async function loadMedia() {
        const container = document.getElementById("media-list");
        container.innerHTML = `<div class="loading-row flex-grow"><span class="loader"></span> 加载转述中...</div>`;

        const params = {
            page: pagination.media.page,
            limit: pagination.media.limit,
            media_type: document.getElementById("media-type").value,
            search: document.getElementById("media-search").value
        };

        try {
            const res = await apiGet("/media", params);
            if (res.status === "success" && res.data) {
                pagination.media.total = res.data.total;
                renderMedia(res.data.items);
                renderPagination("media-pagination", pagination.media, loadMediaPage);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            container.innerHTML = `<div class="no-data-row flex-grow">⚠️ 加载失败: ${e.message}</div>`;
        }
    }

    function loadMediaPage(page) {
        pagination.media.page = page;
        loadMedia();
    }

    function renderMedia(items) {
        const container = document.getElementById("media-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<div class="no-data-row flex-grow">暂无相关媒体转述缓存</div>`;
            return;
        }

        container.innerHTML = items.map(item => {
            let preview = "";
            if (item.media_type === "image" && item.url) {
                preview = `<img src="${item.url}" alt="预览图片" onerror="this.onerror=null; this.src='placeholder.png';">`;
            } else if (item.media_type === "voice" && item.url) {
                preview = `<audio class="media-audio-player" controls src="${item.url}"></audio>`;
            } else {
                preview = `<div style="font-size: 32px;">📄</div>`;
            }

            const encodedCaption = encodeURIComponent(item.caption || "");
            const encodedUrl = encodeURIComponent(item.url || "");
            const encodedGenre = encodeURIComponent(item.genre || "");
            const encodedCharacter = encodeURIComponent(item.character || "");
            const encodedSource = encodeURIComponent(item.source || "");
            const encodedText = encodeURIComponent(item.text || "");

            return `
                <div class="media-card card">
                    <div class="media-preview-box">
                        ${preview}
                    </div>
                    <div class="media-info">
                        <div class="media-caption-text">${escapeHtml(item.caption || "暂无描述内容")}</div>
                        <div style="margin-top: 8px; font-size: 11px; color: var(--font-secondary); word-break: break-all;">
                            <strong>Hash:</strong> ${item.hash_val}
                        </div>
                        <div style="font-size: 11px; color: var(--font-secondary);">
                            <strong>使用次数:</strong> ${item.query_times || 0} 次
                        </div>
                        <div style="font-size: 11px; color: var(--font-secondary);">
                            <strong>转述时间:</strong> ${formatDate(item.created_at)}
                        </div>
                    </div>
                    <div class="media-actions">
                        <button class="btn btn-secondary btn-small" onclick="openEditMediaModal('${item.hash_val}', '${encodedUrl}', '${item.media_type}', '${encodedCaption}', '${encodedGenre}', '${encodedCharacter}', '${encodedSource}', '${encodedText}')">修改描述</button>
                        <button class="btn btn-danger btn-small" onclick="deleteMedia('${item.hash_val}')">删除缓存</button>
                    </div>
                </div>
            `;
        }).join("");
    }

    // ----------------------------------------------------
    // Modal Helpers
    // ----------------------------------------------------
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
        document.getElementById("add-mem-text").value = "";
        openModal("add-memory-modal");
    };

    window.submitAddMemory = async function() {
        const botName = document.getElementById("add-mem-bot").value.trim();
        const groupId = document.getElementById("add-mem-group").value.trim();
        const text = document.getElementById("add-mem-text").value.trim();

        if (!botName || !groupId || !text) {
            showToast("请填写完整参数！");
            return;
        }

        try {
            const res = await apiPost("/memories/add", {
                bot_name: botName,
                group_or_user_id: groupId,
                text: text,
                user_id: "admin"
            });
            if (res.status === "success") {
                showToast("保存记忆成功！");
                closeModal("add-memory-modal");
                loadMemories();
            } else {
                showToast(`保存失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    // 2. Edit Memory
    window.openEditMemoryModal = function(id, bot, group, textEncoded) {
        const text = decodeURIComponent(textEncoded);
        document.getElementById("edit-mem-id").value = id;
        document.getElementById("edit-mem-bot").value = bot;
        document.getElementById("edit-mem-group").value = group;
        document.getElementById("edit-mem-text").value = text;
        openModal("edit-memory-modal");
    };

    window.submitEditMemory = async function() {
        const id = document.getElementById("edit-mem-id").value;
        const bot = document.getElementById("edit-mem-bot").value;
        const group = document.getElementById("edit-mem-group").value;
        const text = document.getElementById("edit-mem-text").value.trim();

        if (!text) {
            showToast("记忆文本不能为空！");
            return;
        }

        try {
            const res = await apiPost("/memories/update", {
                memory_id: id,
                bot_name: bot,
                group_or_user_id: group,
                text: text,
                user_id: "admin"
            });
            if (res.status === "success") {
                showToast("保存成功！");
                closeModal("edit-memory-modal");
                loadMemories();
            } else {
                showToast(`更新失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    // 3. Delete Memory
    window.deleteMemory = function(id) {
        showConfirm("确认删除长期记忆", "确定要删除这条长期记忆吗？此操作无法撤销。", async () => {
            try {
                const res = await apiPost("/memories/delete", { memory_id: id });
                if (res.status === "success") {
                    showToast("记忆删除成功");
                    loadMemories();
                } else {
                    showToast(`删除失败: ${res.message}`);
                }
            } catch (e) {
                showToast(`发生错误: ${e.message}`);
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
        document.getElementById("edit-media-hash-display").value = hash;
        document.getElementById("edit-media-caption").value = caption;
        document.getElementById("edit-media-genre").value = genre;
        document.getElementById("edit-media-character").value = character;
        document.getElementById("edit-media-source").value = source;
        document.getElementById("edit-media-text").value = text;
        
        const previewContainer = document.getElementById("edit-media-preview");
        if (type === "image" && url) {
            previewContainer.innerHTML = `<img src="${url}" alt="预览">`;
        } else if (type === "voice" && url) {
            previewContainer.innerHTML = `<audio controls src="${url}"></audio>`;
        } else {
            previewContainer.innerHTML = `<div style="font-size: 24px;">📄</div>`;
        }
        
        openModal("edit-media-modal");
    };

    window.submitEditMedia = async function() {
        const hash = document.getElementById("edit-media-hash").value;
        const caption = document.getElementById("edit-media-caption").value.trim();
        const genre = document.getElementById("edit-media-genre").value.trim();
        const character = document.getElementById("edit-media-character").value.trim();
        const source = document.getElementById("edit-media-source").value.trim();
        const text = document.getElementById("edit-media-text").value.trim();

        if (!caption) {
            showToast("描述内容不能为空！");
            return;
        }

        try {
            const res = await apiPost("/media/update", {
                hash_val: hash,
                caption: caption,
                genre: genre,
                character: character,
                source: source,
                text: text
            });
            if (res.status === "success") {
                showToast("更新成功！");
                closeModal("edit-media-modal");
                loadMedia();
            } else {
                showToast(`保存失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    // 5. Delete Media Caption
    window.deleteMedia = function(hash) {
        showConfirm("确认清理媒体缓存", "确定要清理这条媒体缓存吗？这将清空它的大模型文字转述内容。", async () => {
            try {
                const res = await apiPost("/media/delete", { hash_val: hash });
                if (res.status === "success") {
                    showToast("媒体描述已清理");
                    loadMedia();
                } else {
                    showToast(`清理失败: ${res.message}`);
                }
            } catch (e) {
                showToast(`发生错误: ${e.message}`);
            }
        });
    };

    // 6. Fill energy
    window.fillEnergy = async function(bot, group) {
        try {
            const res = await apiPost("/status/fill_energy", {
                bot_name: bot,
                group_or_user_id: group
            });
            if (res.status === "success") {
                showToast(`成功为 ${bot} 充满能量！`);
                loadBotStatus();
            } else {
                showToast(`补充能量失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    // 7. Edit Bot Status
    window.openEditStatusModal = function(bot, group, mood, state) {
        document.getElementById("edit-status-bot").value = bot;
        document.getElementById("edit-status-group").value = group;
        document.getElementById("edit-status-mood").value = mood;
        document.getElementById("edit-status-state").value = state;
        openModal("edit-status-modal");
    };

    window.submitEditStatus = async function() {
        const bot = document.getElementById("edit-status-bot").value;
        const group = document.getElementById("edit-status-group").value;
        const mood = document.getElementById("edit-status-mood").value.trim();
        const state = document.getElementById("edit-status-state").value.trim();

        try {
            const res = await apiPost("/status/update", {
                bot_name: bot,
                group_or_user_id: group,
                mood: mood,
                state: state
            });
            if (res.status === "success") {
                showToast("Bot 状态调整成功！");
                closeModal("edit-status-modal");
                loadBotStatus();
            } else {
                showToast(`修改失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    // ----------------------------------------------------
    // Utilities
    // ----------------------------------------------------
    function formatDate(isoString) {
        if (!isoString) return "-";
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) {
                // Try format YYYY-MM-DD HH:MM:SS
                return isoString;
            }
            const y = date.getFullYear();
            const m = String(date.getMonth() + 1).padStart(2, "0");
            const d = String(date.getDate()).padStart(2, "0");
            const h = String(date.getHours()).padStart(2, "0");
            const min = String(date.getMinutes()).padStart(2, "0");
            const s = String(date.getSeconds()).padStart(2, "0");
            return `${y}-${m}-${d} ${h}:${min}:${s}`;
        } catch {
            return isoString;
        }
    }

    function escapeHtml(text) {
        if (!text) return "";
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function showToast(message) {
        const toast = document.getElementById("toast-message");
        if (toast) {
            toast.textContent = message;
            toast.classList.add("show");
            setTimeout(() => {
                toast.classList.remove("show");
            }, 3000);
        }
    }

    function renderPagination(containerId, state, onPageChange) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const totalPages = Math.ceil(state.total / state.limit);
        if (totalPages <= 1) {
            container.innerHTML = `<span>共 ${state.total} 条记录</span>`;
            return;
        }

        const startItem = (state.page - 1) * state.limit + 1;
        const endItem = Math.min(state.page * state.limit, state.total);

        // Build page buttons (show current page, prev, next, first, last if appropriate)
        let buttons = "";
        
        buttons += `<button class="pagination-btn" ${state.page === 1 ? "disabled" : ""} data-page="${state.page - 1}">上一页</button>`;
        
        // Show up to 5 page numbers
        const startPage = Math.max(1, state.page - 2);
        const endPage = Math.min(totalPages, state.page + 2);
        
        for (let i = startPage; i <= endPage; i++) {
            buttons += `<button class="pagination-btn ${state.page === i ? "active" : ""}" data-page="${i}">${i}</button>`;
        }
        
        buttons += `<button class="pagination-btn" ${state.page === totalPages ? "disabled" : ""} data-page="${state.page + 1}">下一页</button>`;

        container.innerHTML = `
            <span>显示 ${startItem}-${endItem} 条，共 ${state.total} 条</span>
            <div class="pagination-buttons">
                ${buttons}
            </div>
        `;

        // Add event listeners to buttons
        const btns = container.querySelectorAll(".pagination-btn");
        btns.forEach(btn => {
            btn.addEventListener("click", () => {
                const targetPage = parseInt(btn.getAttribute("data-page"));
                if (targetPage && targetPage !== state.page) {
                    onPageChange(targetPage);
                }
            });
        });
    }

    // ----------------------------------------------------
    // TAB 5: Profiles (User and Group)
    // ----------------------------------------------------
    function loadProfilesData() {
        if (activeSubTab === "user-profiles") {
            loadUserProfiles();
        } else if (activeSubTab === "group-profiles") {
            loadGroupProfiles();
        }
    }

    async function loadUserProfiles() {
        const listContainer = document.getElementById("user-profile-list");
        listContainer.innerHTML = `<tr><td colspan="8" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: pagination.userProfiles.page,
            limit: pagination.userProfiles.limit,
            bot_name: document.getElementById("user-profile-bot-name").value,
            group_or_user_id: document.getElementById("user-profile-group-id").value,
            user_id: document.getElementById("user-profile-user-id").value,
            search: document.getElementById("user-profile-search").value
        };

        try {
            const res = await apiGet("/profiles/user", params);
            if (res.status === "success" && res.data) {
                pagination.userProfiles.total = res.data.total;
                renderUserProfiles(res.data.items);
                renderPagination("user-profile-pagination", pagination.userProfiles, loadUserProfilesPage);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="8" class="no-data-row">⚠️ 加载数据失败: ${e.message}</td></tr>`;
        }
    }

    function loadUserProfilesPage(page) {
        pagination.userProfiles.page = page;
        loadUserProfiles();
    }

    function renderUserProfiles(items) {
        const container = document.getElementById("user-profile-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="8" class="no-data-row">暂无相关用户画像记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedProfile = encodeURIComponent(item.profile || "");
            
            let relationBadge = "";
            const rel = parseInt(item.relation) || 0;
            if (rel > 0) {
                relationBadge = `<span class="badge badge-success">+${rel}</span>`;
            } else if (rel < 0) {
                relationBadge = `<span class="badge badge-danger">${rel}</span>`;
            } else {
                relationBadge = `<span class="badge badge-secondary">0</span>`;
            }
            const titleHtml = item.title ? `<span class="badge badge-info">${escapeHtml(item.title)}</span>` : `<span style="color: var(--font-secondary);">-</span>`;

            const encodedTitle = encodeURIComponent(item.title || "");

            return `
                <tr>
                    <td style="font-weight: 600;">${item.bot_name}</td>
                    <td>${item.group_or_user_id}</td>
                    <td>${item.user_id}</td>
                    <td>${relationBadge}</td>
                    <td>${titleHtml}</td>
                    <td>
                        <div style="max-width: 500px; word-break: break-all; white-space: pre-wrap;">${escapeHtml(item.profile || "")}</div>
                    </td>
                    <td>${formatDate(item.updated_at || item.created_at)}</td>
                    <td class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="openEditUserProfileModal('${item.bot_name}', '${item.group_or_user_id}', '${item.user_id}', '${encodedProfile}', ${rel}, '${encodedTitle}')">编辑</button>
                        <button class="btn btn-danger btn-small" onclick="deleteUserProfile('${item.bot_name}', '${item.group_or_user_id}', '${item.user_id}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    }

    async function loadGroupProfiles() {
        const listContainer = document.getElementById("group-profile-list");
        listContainer.innerHTML = `<tr><td colspan="5" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: pagination.groupProfiles.page,
            limit: pagination.groupProfiles.limit,
            bot_name: document.getElementById("group-profile-bot-name").value,
            group_or_user_id: document.getElementById("group-profile-group-id").value,
            search: document.getElementById("group-profile-search").value
        };

        try {
            const res = await apiGet("/profiles/group", params);
            if (res.status === "success" && res.data) {
                pagination.groupProfiles.total = res.data.total;
                renderGroupProfiles(res.data.items);
                renderPagination("group-profile-pagination", pagination.groupProfiles, loadGroupProfilesPage);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="5" class="no-data-row">⚠️ 加载数据失败: ${e.message}</td></tr>`;
        }
    }

    function loadGroupProfilesPage(page) {
        pagination.groupProfiles.page = page;
        loadGroupProfiles();
    }

    function renderGroupProfiles(items) {
        const container = document.getElementById("group-profile-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="5" class="no-data-row">暂无相关群聊画像记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedProfile = encodeURIComponent(item.profile || "");
            return `
                <tr>
                    <td style="font-weight: 600;">${item.bot_name}</td>
                    <td>${item.group_or_user_id}</td>
                    <td>
                        <div style="max-width: 600px; word-break: break-all; white-space: pre-wrap;">${escapeHtml(item.profile || "")}</div>
                    </td>
                    <td>${formatDate(item.updated_at || item.created_at)}</td>
                    <td class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="openEditGroupProfileModal('${item.bot_name}', '${item.group_or_user_id}', '${encodedProfile}')">编辑</button>
                        <button class="btn btn-danger btn-small" onclick="deleteGroupProfile('${item.bot_name}', '${item.group_or_user_id}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    }

    // Modal Actions for Profiles
    window.openEditUserProfileModal = function(bot, group, user, profileEncoded, relation, titleEncoded) {
        const profile = decodeURIComponent(profileEncoded);
        const title = decodeURIComponent(titleEncoded || "");
        document.getElementById("edit-user-prof-bot").value = bot;
        document.getElementById("edit-user-prof-group").value = group;
        document.getElementById("edit-user-prof-user").value = user;
        document.getElementById("edit-user-prof-relation").value = relation !== undefined ? relation : 0;
        document.getElementById("edit-user-prof-title").value = title;
        document.getElementById("edit-user-prof-text").value = profile;
        openModal("edit-user-profile-modal");
    };

    window.submitEditUserProfile = async function() {
        const bot = document.getElementById("edit-user-prof-bot").value;
        const group = document.getElementById("edit-user-prof-group").value;
        const user = document.getElementById("edit-user-prof-user").value;
        const relationVal = document.getElementById("edit-user-prof-relation").value;
        const title = document.getElementById("edit-user-prof-title").value.trim();
        const profile = document.getElementById("edit-user-prof-text").value.trim();

        if (!profile) {
            showToast("画像内容不能为空！");
            return;
        }

        const relation = relationVal !== "" ? parseInt(relationVal) : 0;

        try {
            const res = await apiPost("/profiles/user/update", {
                bot_name: bot,
                group_or_user_id: group,
                user_id: user,
                profile: profile,
                relation: relation,
                title: title
            });
            if (res.status === "success") {
                showToast("保存成功！");
                closeModal("edit-user-profile-modal");
                loadUserProfiles();
            } else {
                showToast(`更新失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    window.deleteUserProfile = function(bot, group, user) {
        showConfirm("确认删除用户画像", "确定要删除该用户的画像总结吗？此操作不可逆。", async () => {
            try {
                const res = await apiPost("/profiles/user/delete", {
                    bot_name: bot,
                    group_or_user_id: group,
                    user_id: user
                });
                if (res.status === "success") {
                    showToast("删除画像成功");
                    loadUserProfiles();
                } else {
                    showToast(`删除失败: ${res.message}`);
                }
            } catch (e) {
                showToast(`发生错误: ${e.message}`);
            }
        });
    };

    window.openEditGroupProfileModal = function(bot, group, profileEncoded) {
        const profile = decodeURIComponent(profileEncoded);
        document.getElementById("edit-group-prof-bot").value = bot;
        document.getElementById("edit-group-prof-group").value = group;
        document.getElementById("edit-group-prof-text").value = profile;
        openModal("edit-group-profile-modal");
    };

    window.submitEditGroupProfile = async function() {
        const bot = document.getElementById("edit-group-prof-bot").value;
        const group = document.getElementById("edit-group-prof-group").value;
        const profile = document.getElementById("edit-group-prof-text").value.trim();

        if (!profile) {
            showToast("画像内容不能为空！");
            return;
        }

        try {
            const res = await apiPost("/profiles/group/update", {
                bot_name: bot,
                group_or_user_id: group,
                profile: profile
            });
            if (res.status === "success") {
                showToast("保存成功！");
                closeModal("edit-group-profile-modal");
                loadGroupProfiles();
            } else {
                showToast(`更新失败: ${res.message}`);
            }
        } catch (e) {
            showToast(`发生错误: ${e.message}`);
        }
    };

    window.deleteGroupProfile = function(bot, group) {
        showConfirm("确认删除群聊画像", "确定要删除该群聊的画像总结吗？此操作不可逆。", async () => {
            try {
                const res = await apiPost("/profiles/group/delete", {
                    bot_name: bot,
                    group_or_user_id: group
                });
                if (res.status === "success") {
                    showToast("删除画像成功");
                    loadGroupProfiles();
                } else {
                    showToast(`删除失败: ${res.message}`);
                }
            } catch (e) {
                showToast(`发生错误: ${e.message}`);
            }
        });
    };

    // Custom Confirm Modal Logic
    let confirmCallback = null;

    window.showConfirm = function(title, message, callback) {
        document.getElementById("confirm-title").textContent = title;
        document.getElementById("confirm-message").textContent = message;
        confirmCallback = callback;
        openModal("confirm-modal");
    };

    window.closeConfirmModal = function() {
        closeModal("confirm-modal");
        confirmCallback = null;
    };

    const confirmBtnOk = document.getElementById("confirm-btn-ok");
    if (confirmBtnOk) {
        confirmBtnOk.addEventListener("click", () => {
            if (confirmCallback) {
                confirmCallback();
            }
            closeConfirmModal();
        });
    }
});
