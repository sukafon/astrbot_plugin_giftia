// Giftia Dashboard Frontend Logic - Main Coordinator

window.GiftiaApp = {
    // Current state variables
    activeTab: "chat-history",
    activeSubTab: "user-profiles",
    
    // Pagination states
    pagination: {
        history: { page: 1, limit: 15, total: 0 },
        memories: { page: 1, limit: 15, total: 0 },
        media: { page: 1, limit: 12, total: 0 },
        userProfiles: { page: 1, limit: 15, total: 0 },
        groupProfiles: { page: 1, limit: 15, total: 0 }
    },

    loadedOriginalMediaG: new Set(),

    // Helper: Load data based on active tab
    loadActiveTabData() {
        if (this.activeTab === "chat-history") {
            this.loadChatHistory();
        } else if (this.activeTab === "memories") {
            this.loadMemories();
        } else if (this.activeTab === "bot-status") {
            this.loadBotStatus();
        } else if (this.activeTab === "media-captions") {
            this.loadMedia();
        } else if (this.activeTab === "profiles") {
            this.loadProfilesData();
        }
    },

    // ----------------------------------------------------
    // TAB 1: Chat History
    // ----------------------------------------------------
    async loadChatHistory() {
        const listContainer = document.getElementById("history-list");
        listContainer.innerHTML = `<tr><td colspan="6" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: this.pagination.history.page,
            limit: this.pagination.history.limit,
            bot_name: document.getElementById("history-bot-name").value,
            group_or_user_id: document.getElementById("history-group-id").value,
            user_id: document.getElementById("history-user-id").value,
            reply_decision: document.getElementById("history-decision").value,
            use_rag: document.getElementById("history-rag").value,
            search: document.getElementById("history-search").value
        };

        try {
            const res = await window.apiGet("/chat_history", params);
            if (res.status === "success" && res.data) {
                this.pagination.history.total = res.data.total;
                this.renderChatHistory(res.data.items);
                window.renderPagination("history-pagination", this.pagination.history, (page) => {
                    this.pagination.history.page = page;
                    this.loadChatHistory();
                });
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="6" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderChatHistory(items) {
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

            const senderDisp = item.nickname ? `${item.nickname} (${item.user_id})` : item.user_id;

            return `
                <tr>
                    <td data-label="时间" style="white-space: nowrap;">${window.formatDate(item.created_at)}</td>
                    <td data-label="机器人" style="font-weight: 600;">${item.bot_name}</td>
                    <td data-label="发送人/会话">
                        <div style="font-size: 13px;">${senderDisp}</div>
                        <div style="font-size: 11px; color: var(--font-secondary);">群组: ${item.group_or_user_id}</div>
                    </td>
                    <td data-label="消息内容">
                        <div style="max-width: 480px; word-break: break-all;">${window.escapeHtml(item.content)}</div>
                    </td>
                    <td data-label="判定结果">${decisionBadge}</td>
                    <td data-label="RAG状态">${ragBadge}</td>
                </tr>
            `;
        }).join("");
    },

    // ----------------------------------------------------
    // TAB 2: Memories
    // ----------------------------------------------------
    async loadMemories() {
        const listContainer = document.getElementById("memory-list");
        listContainer.innerHTML = `<tr><td colspan="5" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: this.pagination.memories.page,
            limit: this.pagination.memories.limit,
            bot_name: document.getElementById("memory-bot-name").value,
            group_or_user_id: document.getElementById("memory-group-id").value,
            search: document.getElementById("memory-search").value
        };

        try {
            const res = await window.apiGet("/memories", params);
            if (res.status === "success" && res.data) {
                this.pagination.memories.total = res.data.total;
                this.renderMemories(res.data.items);
                window.renderPagination("memory-pagination", this.pagination.memories, (page) => {
                    this.pagination.memories.page = page;
                    this.loadMemories();
                });
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="6" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderMemories(items) {
        const container = document.getElementById("memory-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="6" class="no-data-row">暂无相关长期记忆记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedText = encodeURIComponent(item.text);
            const associatedUserIds = item.metadata && item.metadata.associated_user_ids
                ? item.metadata.associated_user_ids.join(', ')
                : (item.metadata && item.metadata.user_id ? item.metadata.user_id : '-');
            const associatedUserIdsList = item.metadata && item.metadata.associated_user_ids
                ? item.metadata.associated_user_ids.join(',')
                : '';
            return `
                <tr>
                    <td data-label="Bot" style="font-weight: 600;">${item.bot_name}</td>
                    <td data-label="群聊/用户ID">${item.group_or_user_id}</td>
                    <td data-label="记忆内容 (Text)">
                        <div style="max-width: 550px; word-break: break-all;">${window.escapeHtml(item.text)}</div>
                    </td>
                    <td data-label="关联用户">${associatedUserIds}</td>
                    <td data-label="创建时间">${window.formatDate(item.created_at)}</td>
                    <td data-label="操作" class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="window.openEditMemoryModal('${item.memory_id}', '${item.bot_name}', '${item.group_or_user_id}', '${encodedText}', '${associatedUserIdsList}')">编辑</button>
                        <button class="btn btn-danger btn-small" onclick="window.deleteMemory('${item.memory_id}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    },

    // ----------------------------------------------------
    // TAB 3: Bot Status
    // ----------------------------------------------------
    async loadBotStatus() {
        const container = document.getElementById("status-grid");
        container.innerHTML = `<div class="loading-row flex-grow"><span class="loader"></span> 加载状态中...</div>`;

        try {
            const res = await window.apiGet("/status");
            if (res.status === "success" && res.data) {
                this.renderBotStatus(res.data);
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            container.innerHTML = `<div class="no-data-row flex-grow">加载状态失败: ${e.message}</div>`;
        }
    },

    renderBotStatus(items) {
        const container = document.getElementById("status-grid");
        if (!items || items.length === 0) {
            container.innerHTML = `<div class="no-data-row flex-grow">目前暂无活动的会话状态，请让机器人先和群友聊天试试吧！</div>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodeStatusArg = value => encodeURIComponent(value || "").replace(/'/g, "%27");
            const energy = Math.max(0, Math.min(100, parseFloat(item.energy) || 0));
            const energyClass = energy < 20 ? "low-energy" : "";
            const mood = item.mood || "平静";
            const state = item.state || "发呆";
            const memory = item.memory || "无";
            const action = item.action || "无";
            const botArg = encodeStatusArg(item.bot_name);
            const groupArg = encodeStatusArg(item.group_or_user_id);
            
            return `
                <div class="status-card card">
                    <div class="status-card-header">
                        <div class="status-card-titles">
                            <h3 class="status-card-title">${window.escapeHtml(item.bot_name)}</h3>
                            <div class="status-card-subtitle-row">
                                <span class="status-card-subtitle" title="${window.escapeHtml(item.group_or_user_id)}">
                                    会话: ${window.escapeHtml(item.group_or_user_id)}
                                </span>
                                <button class="btn-copy-icon" onclick="window.copyToClipboard(decodeURIComponent('${groupArg}'), '会话 ID')" title="复制会话 ID">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                </button>
                            </div>
                        </div>
                        <div class="status-energy-badge ${energyClass}">
                            <span class="energy-icon">
                                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
                            </span>
                            <strong>${energy.toFixed(1)}%</strong>
                        </div>
                    </div>

                    <div class="status-badges-row">
                        <span class="status-pill status-pill-mood">
                            <span class="pill-label">心情</span>
                            <span class="pill-value">${window.escapeHtml(mood)}</span>
                        </span>
                        <span class="status-pill status-pill-state">
                            <span class="pill-label">状态</span>
                            <span class="pill-value">${window.escapeHtml(state)}</span>
                        </span>
                        <span class="status-pill status-pill-action">
                            <span class="pill-label">动作</span>
                            <span class="pill-value">${window.escapeHtml(action)}</span>
                        </span>
                    </div>

                    <div class="status-thought-box">
                        <div class="status-thought-header">
                            <span class="thought-icon">
                                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A5 5 0 0 0 8 8c0 1 .3 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"></path><line x1="9" y1="18" x2="15" y2="18"></line><line x1="10" y1="22" x2="14" y2="22"></line></svg>
                            </span>
                            <span class="thought-label">思考</span>
                        </div>
                        <div class="status-thought-content">${window.escapeHtml(memory)}</div>
                    </div>

                    <div class="status-actions">
                        <button class="btn btn-secondary btn-small" onclick="window.openEditStatusModal('${botArg}', '${groupArg}', '${encodeStatusArg(mood)}', '${encodeStatusArg(state)}', '${encodeStatusArg(memory)}', '${encodeStatusArg(action)}')">调整状态</button>
                        <button class="btn btn-primary btn-small" onclick="window.fillEnergy('${botArg}', '${groupArg}')">补满能量</button>
                    </div>
                </div>
            `;
        }).join("");
    },

    // ----------------------------------------------------
    // TAB 4: Media Captions
    // ----------------------------------------------------
    async loadMedia() {
        const container = document.getElementById("media-list");
        container.innerHTML = `<div class="loading-row flex-grow"><span class="loader"></span> 加载转述中...</div>`;

        const params = {
            page: this.pagination.media.page,
            limit: this.pagination.media.limit,
            media_type: document.getElementById("media-type").value,
            search: document.getElementById("media-search").value
        };

        try {
            const res = await window.apiGet("/media", params);
            if (res.status === "success" && res.data) {
                this.pagination.media.total = res.data.total;
                this.renderMedia(res.data.items);
                window.renderPagination("media-pagination", this.pagination.media, (page) => {
                    this.pagination.media.page = page;
                    this.loadMedia();
                });
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            container.innerHTML = `<div class="no-data-row flex-grow">加载失败: ${e.message}</div>`;
        }
    },

    async loadMediaFileB64(hash, elementId, fallbackUrl, type, isThumbnail = false) {
        const el = document.getElementById(elementId);
        if (!el) return;

        try {
            const endpoint = isThumbnail ? `/media/file/thumbnail/b64/${hash}` : `/media/file/b64/${hash}`;
            const res = await window.apiGet(endpoint);
            if (res && res.status === "success" && res.base64) {
                const mimeType = res.content_type || (type === "image" ? "image/jpeg" : "audio/mpeg");
                el.src = `data:${mimeType};base64,${res.base64}`;
            } else {
                if (type === "image") {
                    el.onerror = () => {
                        el.onerror = null;
                        el.src = 'placeholder.png';
                    };
                    el.src = fallbackUrl || 'placeholder.png';
                } else if (fallbackUrl) {
                    el.src = fallbackUrl;
                }
            }
        } catch (e) {
            console.error("Failed to load media base64 for hash:", hash, e);
            if (type === "image") {
                el.onerror = () => {
                    el.onerror = null;
                    el.src = 'placeholder.png';
                };
                el.src = fallbackUrl || 'placeholder.png';
            } else if (fallbackUrl) {
                el.src = fallbackUrl;
            }
        }
    },

    renderMedia(items) {
        const container = document.getElementById("media-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<div class="no-data-row flex-grow">暂无相关媒体转述缓存</div>`;
            return;
        }

        container.innerHTML = items.map(item => {
            let preview = "";
            const uniqueId = `media-preview-${item.hash_val}`;
            if (item.media_type === "image" && item.url) {
                preview = `<img id="${uniqueId}" src="placeholder.png" alt="加载中...">`;
            } else if ((item.media_type === "audio" || item.media_type === "voice") && item.url) {
                preview = `<audio id="${uniqueId}" class="media-audio-player" controls></audio>`;
            } else {
                preview = `<div style="font-size: 32px;">📄</div>`;
            }

            const encodeMediaArg = value => encodeURIComponent(value || "").replace(/'/g, "%27");
            const encodedCaption = encodeMediaArg(item.caption);
            const encodedUrl = encodeMediaArg(item.url);
            const encodedGenre = encodeMediaArg(item.genre);
            const encodedCharacter = encodeMediaArg(item.character);
            const encodedSource = encodeMediaArg(item.source);
            const encodedText = encodeMediaArg(item.text);

            const badgeClass = item.is_captioned ? "badge-success" : "badge-secondary";
            const badgeText = item.is_captioned ? "已转述" : "待转述";

            return `
                <div class="media-card card">
                    <div class="media-preview-box">
                        ${preview}
                        <span class="badge ${badgeClass}" style="position: absolute; top: 8px; right: 8px; z-index: 10;">${badgeText}</span>
                    </div>
                    <div class="media-info">
                        <div class="media-caption-text">${window.escapeHtml(item.caption || "暂无描述内容")}</div>
                        <div style="margin-top: 8px; font-size: 11px; color: var(--font-secondary);">
                            <strong>风格类型:</strong> ${window.escapeHtml(item.genre || "未知")}
                        </div>
                        <div style="font-size: 11px; color: var(--font-secondary);">
                            <strong>使用次数:</strong> ${item.query_times || 0} 次
                        </div>
                        <div style="font-size: 11px; color: var(--font-secondary);">
                            <strong>转述时间:</strong> ${window.formatDate(item.created_at)}
                        </div>
                    </div>
                    <div class="media-actions">
                        <button class="btn btn-secondary btn-small" onclick="window.openEditMediaModal('${item.hash_val}', '${encodedUrl}', '${item.media_type}', '${encodedCaption}', '${encodedGenre}', '${encodedCharacter}', '${encodedSource}', '${encodedText}')">修改描述</button>
                        <button class="btn btn-danger btn-small" onclick="window.deleteMedia('${item.hash_val}')">删除缓存</button>
                    </div>
                </div>
            `;
        }).join("");

        const hoverTimers = new Map();

        items.forEach(item => {
            if (item.url && (item.media_type === "image" || item.media_type === "audio" || item.media_type === "voice")) {
                const uniqueId = `media-preview-${item.hash_val}`;
                const isImg = item.media_type === "image";
                const shouldLoadThumb = isImg && !this.loadedOriginalMediaG.has(item.hash_val);

                this.loadMediaFileB64(item.hash_val, uniqueId, item.url, item.media_type, shouldLoadThumb);

                if (isImg) {
                    const imgEl = document.getElementById(uniqueId);
                    const previewBox = imgEl ? imgEl.closest(".media-preview-box") : null;
                    if (previewBox) {
                        previewBox.addEventListener("mouseenter", () => {
                            if (this.loadedOriginalMediaG.has(item.hash_val)) return;

                            const timer = setTimeout(() => {
                                this.loadMediaFileB64(item.hash_val, uniqueId, item.url, item.media_type, false);
                                this.loadedOriginalMediaG.add(item.hash_val);
                            }, 500);
                            hoverTimers.set(item.hash_val, timer);
                        });

                        previewBox.addEventListener("mouseleave", () => {
                            if (hoverTimers.has(item.hash_val)) {
                                clearTimeout(hoverTimers.get(item.hash_val));
                                hoverTimers.delete(item.hash_val);
                            }
                        });
                    }
                }
            }
        });
    },

    // ----------------------------------------------------
    // TAB 5: Profiles (User and Group)
    // ----------------------------------------------------
    loadProfilesData() {
        if (this.activeSubTab === "user-profiles") {
            this.loadUserProfiles();
        } else if (this.activeSubTab === "group-profiles") {
            this.loadGroupProfiles();
        }
    },

    async loadUserProfiles() {
        const listContainer = document.getElementById("user-profile-list");
        listContainer.innerHTML = `<tr><td colspan="8" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: this.pagination.userProfiles.page,
            limit: this.pagination.userProfiles.limit,
            bot_name: document.getElementById("user-profile-bot-name").value,
            group_or_user_id: document.getElementById("user-profile-group-id").value,
            user_id: document.getElementById("user-profile-user-id").value,
            search: document.getElementById("user-profile-search").value
        };

        try {
            const res = await window.apiGet("/profiles/user", params);
            if (res.status === "success" && res.data) {
                this.pagination.userProfiles.total = res.data.total;
                this.renderUserProfiles(res.data.items);
                window.renderPagination("user-profile-pagination", this.pagination.userProfiles, (page) => {
                    this.pagination.userProfiles.page = page;
                    this.loadUserProfiles();
                });
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="8" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderUserProfiles(items) {
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
            const titleHtml = item.title ? `<span class="badge badge-info">${window.escapeHtml(item.title)}</span>` : `<span style="color: var(--font-secondary);">-</span>`;
            const encodedTitle = encodeURIComponent(item.title || "");

            return `
                <tr>
                    <td data-label="Bot" style="font-weight: 600;">${item.bot_name}</td>
                    <td data-label="群聊/会话ID">${item.group_or_user_id}</td>
                    <td data-label="用户ID">${item.user_id}</td>
                    <td data-label="好感度">${relationBadge}</td>
                    <td data-label="关系头衔">${titleHtml}</td>
                    <td data-label="画像总结内容 (Profile)">
                        <div style="max-width: 500px; word-break: break-all; white-space: pre-wrap;">${window.escapeHtml(item.profile || "")}</div>
                    </td>
                    <td data-label="更新时间">${window.formatDate(item.updated_at || item.created_at)}</td>
                    <td data-label="操作" class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="window.openEditUserProfileModal('${item.bot_name}', '${item.group_or_user_id}', '${item.user_id}', '${encodedProfile}', ${rel}, '${encodedTitle}')">编辑</button>
                        <button class="btn btn-danger btn-small" onclick="window.deleteUserProfile('${item.bot_name}', '${item.group_or_user_id}', '${item.user_id}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    },

    async loadGroupProfiles() {
        const listContainer = document.getElementById("group-profile-list");
        listContainer.innerHTML = `<tr><td colspan="5" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;

        const params = {
            page: this.pagination.groupProfiles.page,
            limit: this.pagination.groupProfiles.limit,
            bot_name: document.getElementById("group-profile-bot-name").value,
            group_or_user_id: document.getElementById("group-profile-group-id").value,
            search: document.getElementById("group-profile-search").value
        };

        try {
            const res = await window.apiGet("/profiles/group", params);
            if (res.status === "success" && res.data) {
                this.pagination.groupProfiles.total = res.data.total;
                this.renderGroupProfiles(res.data.items);
                window.renderPagination("group-profile-pagination", this.pagination.groupProfiles, (page) => {
                    this.pagination.groupProfiles.page = page;
                    this.loadGroupProfiles();
                });
            } else {
                throw new Error(res.message || "请求失败");
            }
        } catch (e) {
            listContainer.innerHTML = `<tr><td colspan="5" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderGroupProfiles(items) {
        const container = document.getElementById("group-profile-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="5" class="no-data-row">暂无相关群聊画像记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedProfile = encodeURIComponent(item.profile || "");
            return `
                <tr>
                    <td data-label="Bot" style="font-weight: 600;">${item.bot_name}</td>
                    <td data-label="群聊/会话ID">${item.group_or_user_id}</td>
                    <td data-label="群聊画像总结内容 (Profile)">
                        <div style="max-width: 600px; word-break: break-all; white-space: pre-wrap;">${window.escapeHtml(item.profile || "")}</div>
                    </td>
                    <td data-label="更新时间">${window.formatDate(item.updated_at || item.created_at)}</td>
                    <td data-label="操作" class="text-right">
                        <button class="btn btn-secondary btn-small" onclick="window.openEditGroupProfileModal('${item.bot_name}', '${item.group_or_user_id}', '${encodedProfile}')">编辑</button>
                        <button class="btn btn-danger btn-small" onclick="window.deleteGroupProfile('${item.bot_name}', '${item.group_or_user_id}')">删除</button>
                    </td>
                </tr>
            `;
        }).join("");
    }
};

document.addEventListener("DOMContentLoaded", () => {
    // Initialize AstrBot Bridge SDK
    if (window.AstrBotPluginPage) {
        window.AstrBotPluginPage.ready().then((context) => {
            console.log("AstrBot Plugin Bridge Ready. Context:", context);
            if (context && typeof context.isDark === "boolean") {
                document.documentElement.setAttribute(
                    "data-theme",
                    context.isDark ? "dark" : "light"
                );
            }
            window.GiftiaApp.loadActiveTabData();
        }).catch((err) => {
            console.error("Failed to initialize AstrBot Bridge SDK:", err);
            window.GiftiaApp.loadActiveTabData();
        });
        
        window.AstrBotPluginPage.onContext((ctx) => {
            if (ctx && typeof ctx.isDark === "boolean") {
                document.documentElement.setAttribute(
                    "data-theme",
                    ctx.isDark ? "dark" : "light"
                );
            }
        });
    } else {
        window.GiftiaApp.loadActiveTabData();
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
            
            window.GiftiaApp.activeTab = targetTab;
            window.GiftiaApp.loadActiveTabData();
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
            
            window.GiftiaApp.activeSubTab = targetSubTab;
            window.GiftiaApp.loadProfilesData();
        });
    });

    // Tab switching for Edit Media Modal
    document.addEventListener("click", (e) => {
        const btn = e.target.closest(".media-tab-btn");
        if (btn) {
            const tabName = btn.getAttribute("data-mediatab");
            const parent = btn.closest(".edit-media-tabs");
            if (parent) {
                parent.querySelectorAll(".media-tab-btn").forEach(b => b.classList.remove("active"));
                parent.querySelectorAll(".media-tab-panel").forEach(p => p.classList.remove("active"));
                
                btn.classList.add("active");
                const targetPanel = parent.querySelector(`#mediatab-${tabName}`);
                if (targetPanel) {
                    targetPanel.classList.add("active");
                }
            }
        }
    });

    // Filter Listeners (Debounced)
    let filterTimeout;
    const filterInputIds = [
        "history-bot-name", "history-group-id", "history-user-id", "history-decision", "history-rag", "history-search",
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
                    const app = window.GiftiaApp;
                    if (app.activeTab === "chat-history") {
                        app.pagination.history.page = 1;
                        app.loadChatHistory();
                    } else if (app.activeTab === "memories") {
                        app.pagination.memories.page = 1;
                        app.loadMemories();
                    } else if (app.activeTab === "media-captions") {
                        app.pagination.media.page = 1;
                        app.loadMedia();
                    } else if (app.activeTab === "profiles") {
                        if (app.activeSubTab === "user-profiles") {
                            app.pagination.userProfiles.page = 1;
                            app.loadUserProfiles();
                        } else {
                            app.pagination.groupProfiles.page = 1;
                            app.loadGroupProfiles();
                        }
                    }
                }, 300);
            });
        }
    });
});
