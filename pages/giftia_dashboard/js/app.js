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
    filterOptions: {},

    getScopedViewConfig(viewKey) {
        const configs = {
            history: {
                endpoint: "/chat_history",
                filterEndpoint: "/chat_history/filter_options",
                botInputId: "history-bot-name",
                groupInputId: "history-group-id",
                paginationKey: "history",
            },
            memories: {
                endpoint: "/memories",
                filterEndpoint: "/memories/filter_options",
                botInputId: "memory-bot-name",
                groupInputId: "memory-group-id",
                paginationKey: "memories",
            },
            userProfiles: {
                endpoint: "/profiles/user",
                filterEndpoint: "/profiles/user/filter_options",
                botInputId: "profile-bot-name",
                groupInputId: "profile-group-id",
                paginationKey: "userProfiles",
            },
            groupProfiles: {
                endpoint: "/profiles/group",
                filterEndpoint: "/profiles/group/filter_options",
                botInputId: "profile-bot-name",
                groupInputId: "profile-group-id",
                paginationKey: "groupProfiles",
            }
        };
        return configs[viewKey];
    },

    getScopedFilterParams(viewKey) {
        switch (viewKey) {
            case "history":
                return {
                    bot_name: document.getElementById("history-bot-name").value,
                    user_id: document.getElementById("history-user-id").value,
                    reply_decision: document.getElementById("history-decision").value,
                    use_rag: document.getElementById("history-rag").value,
                    search: document.getElementById("history-search").value
                };
            case "memories":
                return {
                    bot_name: document.getElementById("memory-bot-name").value,
                    associated_user_id: document.getElementById("memory-associated-user-id").value,
                    search: document.getElementById("memory-search").value
                };
            case "userProfiles":
                return {
                    bot_name: document.getElementById("profile-bot-name").value,
                    user_id: document.getElementById("profile-user-id").value
                };
            case "groupProfiles":
                return {
                    bot_name: document.getElementById("profile-bot-name").value
                };
            default:
                return {};
        }
    },

    populateBotSelect(selectEl, bots, selectedBotName) {
        if (!selectEl) return;
        selectEl.innerHTML = "";
        if (!bots || bots.length === 0) {
            selectEl.append(new Option("暂无 Bot", ""));
            selectEl.disabled = true;
            return;
        }

        bots.forEach(bot => {
            selectEl.append(new Option(bot, bot));
        });
        selectEl.disabled = false;
        selectEl.value = selectedBotName || bots[0];
    },

    populateSessionSelect(selectEl, sessions, selectedSession) {
        if (!selectEl) return;
        selectEl.innerHTML = "";
        if (!sessions || sessions.length === 0) {
            selectEl.append(new Option("暂无会话", ""));
            selectEl.disabled = true;
            return;
        }

        sessions.forEach(session => {
            const sessionId = session.group_or_user_id || "";
            const total = session.total || 0;
            selectEl.append(new Option(`${sessionId} (${total})`, sessionId));
        });
        selectEl.disabled = false;
        selectEl.value = selectedSession || sessions[0].group_or_user_id || "";
    },

    async refreshScopedFilters(viewKey, preserveSession = true) {
        const config = this.getScopedViewConfig(viewKey);
        if (!config) return;

        const botEl = document.getElementById(config.botInputId);
        const groupEl = document.getElementById(config.groupInputId);
        const currentSession = groupEl ? groupEl.value : "";
        const params = this.getScopedFilterParams(viewKey);

        try {
            const res = await window.apiGet(config.filterEndpoint, params);
            const data = res.status === "success" && res.data ? res.data : { bots: [], sessions: [], selected_bot_name: "" };
            const bots = data.bots || [];
            const selectedBotName = data.selected_bot_name || "";
            const sessions = data.sessions || [];

            this.populateBotSelect(botEl, bots, selectedBotName);

            const nextSession = preserveSession && sessions.some(item => item.group_or_user_id === currentSession)
                ? currentSession
                : (sessions[0] ? sessions[0].group_or_user_id : "");

            if (groupEl) {
                groupEl.value = nextSession;
            }
            this.populateSessionSelect(groupEl, sessions, nextSession);
        } catch (e) {
            if (groupEl) {
                groupEl.value = "";
            }
            this.populateSessionSelect(groupEl, [], "");
        }
    },

    async initializeScopedView(viewKey) {
        await this.refreshScopedFilters(viewKey);
        await this.loadScopedViewData(viewKey);
    },

    async loadScopedViewData(viewKey) {
        switch (viewKey) {
            case "history":
                await this.loadChatHistory();
                break;
            case "memories":
                await this.loadMemories();
                break;
            case "userProfiles":
                await this.loadUserProfiles();
                break;
            case "groupProfiles":
                await this.loadGroupProfiles();
                break;
            default:
                break;
        }
    },

    resetPagination(viewKey) {
        const config = this.getScopedViewConfig(viewKey);
        if (config && this.pagination[config.paginationKey]) {
            this.pagination[config.paginationKey].page = 1;
        }
    },

    // Helper: Load data based on active tab
    loadActiveTabData() {
        if (this.activeTab === "chat-history") {
            this.initializeScopedView("history");
        } else if (this.activeTab === "memories") {
            this.initializeScopedView("memories");
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
        if (!document.getElementById("history-bot-name").value) {
            this.pagination.history.total = 0;
            listContainer.innerHTML = `<tr><td colspan="6" class="no-data-row">暂无可用 Bot</td></tr>`;
            window.renderPagination("history-pagination", this.pagination.history, () => {});
            return;
        }

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
                const lastSummarizedId = res.data.last_summarized_id || 0;
                const boundaryEl = document.getElementById("history-last-summarized-id");
                if (boundaryEl) {
                    boundaryEl.textContent = lastSummarizedId > 0 ? `#${lastSummarizedId}` : "无";
                }
                this.renderChatHistory(res.data.items, lastSummarizedId);
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

    renderChatHistory(items, lastSummarizedId = 0) {
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

            const isSummarized = item.id <= lastSummarizedId;
            const summaryBadge = isSummarized
                ? `<span class="badge badge-success" style="font-size: 0.75rem; padding: 2px 6px;">已归档</span>`
                : `<span class="badge badge-secondary" style="font-size: 0.75rem; padding: 2px 6px;">待总结/跳过</span>`;

            return `
                <tr>
                    <td data-label="ID">
                        <div style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;">
                            <code>#${item.id}</code>
                            ${summaryBadge}
                        </div>
                    </td>
                    <td data-label="时间" style="white-space: nowrap;">${window.formatDate(item.created_at)}</td>
                    <td data-label="发送人">${senderDisp}</td>
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
        listContainer.innerHTML = `<tr><td colspan="4" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;
        if (!document.getElementById("memory-bot-name").value) {
            this.pagination.memories.total = 0;
            listContainer.innerHTML = `<tr><td colspan="4" class="no-data-row">暂无可用 Bot</td></tr>`;
            window.renderPagination("memory-pagination", this.pagination.memories, () => {});
            return;
        }

        const params = {
            page: this.pagination.memories.page,
            limit: this.pagination.memories.limit,
            bot_name: document.getElementById("memory-bot-name").value,
            group_or_user_id: document.getElementById("memory-group-id").value,
            associated_user_id: document.getElementById("memory-associated-user-id").value,
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
            listContainer.innerHTML = `<tr><td colspan="4" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderMemories(items) {
        const container = document.getElementById("memory-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="4" class="no-data-row">暂无相关长期记忆记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedText = encodeURIComponent(item.text);
            const associatedUserIdsArray = item.metadata && Array.isArray(item.metadata.associated_user_ids)
                ? item.metadata.associated_user_ids.filter(Boolean)
                : [];
            const fallbackUserId = item.metadata && item.metadata.user_id
                ? String(item.metadata.user_id)
                : "";
            const associatedUserIdsList = associatedUserIdsArray.length > 0
                ? associatedUserIdsArray.join(',')
                : fallbackUserId;
            const associatedUserIds = associatedUserIdsArray.length > 0
                ? associatedUserIdsArray.join(', ')
                : (fallbackUserId || '-');
            return `
                <tr>
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

    /**
     * 判断音频 MIME 在当前客户端上能否被原生解码。
     *
     * 注意：AMR（QQ 语音）、Silk（微信语音）只有 IM WebView（微信/QQ 浏览器）
     * 才能播放，PC 端 Chrome / Edge / Firefox 全部不支持。所以这里要根据
     * 客户端平台分别处理：
     *  - 移动端 / IM WebView：信任其内置 AMR/Silk 解码器
     *  - PC 浏览器：只信任标准 MIME 白名单
     */
    isClientPlayableAudio(mimeType) {
        if (!mimeType) return false;

        // 1. 标准 MIME（所有浏览器都支持）— 直接放行
        const standardMimes = [
            "audio/mpeg",
            "audio/mp3",
            "audio/wav",
            "audio/wave",
            "audio/x-wav",
            "audio/ogg",
            "audio/flac",
            "audio/x-flac",
            "audio/mp4",
            "audio/aac",
            "audio/x-m4a",
        ];
        if (standardMimes.includes(mimeType)) return true;

        // 2. 移动端 / IM WebView 通常内置 AMR/Silk 解码器
        //    微信: MicroMessenger，QQ: QQ/
        //    通用移动端 UA 也兜底（iOS Safari / Android Chrome 多数 IM WebView）
        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i.test(
            navigator.userAgent || ""
        );
        const isIMWebView = /MicroMessenger|QQ\//i.test(navigator.userAgent || "");

        if (isMobile || isIMWebView) {
            // 移动端 / IM WebView：信任它能播放 AMR/Silk
            if (
                mimeType === "audio/amr" ||
                mimeType === "audio/silk" ||
                mimeType === "audio/x-amr"
            ) {
                return true;
            }
        }

        // 3. PC 浏览器对 AMR/Silk 完全无能为力
        return false;
    },

    /**
     * 兼容旧名（保留调用点向后兼容）
     * @deprecated use isClientPlayableAudio instead
     */
    isPcPlayableAudio(mimeType) {
        return this.isClientPlayableAudio(mimeType);
    },

    /**
     * 把音频预览区替换为"PC 不支持"提示。
     * AMR（QQ 语音）、Silk（微信语音）只有 IM WebView / 移动浏览器内置解码器，
     * PC 端 Chrome / Edge / Firefox 全部无法播放，转写文本会在对话上下文里展示。
     */
    renderAudioUnsupportedNotice(elementId, hash, mimeType) {
        const el = document.getElementById(elementId);
        if (!el) return;
        const wrapper = el.closest(".media-preview-box") || el.parentElement;
        if (!wrapper) return;
        const friendly = mimeType
            ? mimeType.replace("audio/", "").toUpperCase()
            : "未知";
        wrapper.innerHTML = `
            <div class="media-audio-unsupported">
                <div class="media-audio-unsupported-icon">🎧</div>
                <div class="media-audio-unsupported-title">${friendly} 音频</div>
                <div class="media-audio-unsupported-hint">PC 浏览器不支持此格式在线播放<br>（仅移动端 / IM WebView 可播放）</div>
                <a href="#" class="btn btn-secondary btn-small media-audio-download-btn" onclick="window.downloadMedia('${hash}', '${mimeType}'); return false;">
                    📥 下载音频
                </a>
            </div>
        `;
    },

    async loadMediaFileB64(hash, elementId, fallbackUrl, type, isThumbnail = false) {
        const el = document.getElementById(elementId);
        if (!el) return;

        try {
            const endpoint = isThumbnail ? `/media/file/thumbnail/b64/${hash}` : `/media/file/b64/${hash}`;
            const res = await window.apiGet(endpoint);
            if (res && res.status === "success" && res.base64) {
                const mimeType = res.content_type || (type === "image" ? "image/jpeg" : "audio/mpeg");

                // 音频：若客户端无法播放该格式，才展示不支持提示
                if (type === "audio" || type === "voice") {
                    if (!this.isClientPlayableAudio(mimeType)) {
                        this.renderAudioUnsupportedNotice(elementId, hash, mimeType);
                        return;
                    }
                }

                el.src = `data:${mimeType};base64,${res.base64}`;
            } else {
                if (type === "image") {
                    el.onerror = () => {
                        el.onerror = null;
                        el.src = 'placeholder.png';
                    };
                    el.src = fallbackUrl || 'placeholder.png';
                } else if (type === "audio" || type === "voice") {
                    // 失败也按不支持处理，给出下载入口
                    this.renderAudioUnsupportedNotice(elementId, hash, "");
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
            } else if (type === "audio" || type === "voice") {
                this.renderAudioUnsupportedNotice(elementId, hash, "");
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
        this.activeSubTab = document.getElementById("profile-type")?.value || "user-profiles";
        this.updateProfileFilterVisibility();
        if (this.activeSubTab === "user-profiles") {
            this.initializeScopedView("userProfiles");
        } else if (this.activeSubTab === "group-profiles") {
            this.initializeScopedView("groupProfiles");
        }
    },

    updateProfileFilterVisibility() {
        const userFilterGroup = document.getElementById("profile-user-filter-group");
        const userPanel = document.getElementById("subpanel-user-profiles");
        const groupPanel = document.getElementById("subpanel-group-profiles");
        const isUserProfiles = this.activeSubTab === "user-profiles";
        if (userFilterGroup) {
            userFilterGroup.style.display = isUserProfiles ? "" : "none";
        }
        if (userPanel) {
            userPanel.classList.toggle("active", isUserProfiles);
        }
        if (groupPanel) {
            groupPanel.classList.toggle("active", !isUserProfiles);
        }
    },

    async loadUserProfiles() {
        const listContainer = document.getElementById("user-profile-list");
        listContainer.innerHTML = `<tr><td colspan="6" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;
        if (!document.getElementById("profile-bot-name").value) {
            this.pagination.userProfiles.total = 0;
            listContainer.innerHTML = `<tr><td colspan="6" class="no-data-row">暂无可用 Bot</td></tr>`;
            window.renderPagination("user-profile-pagination", this.pagination.userProfiles, () => {});
            return;
        }

        const params = {
            page: this.pagination.userProfiles.page,
            limit: this.pagination.userProfiles.limit,
            bot_name: document.getElementById("profile-bot-name").value,
            group_or_user_id: document.getElementById("profile-group-id").value,
            user_id: document.getElementById("profile-user-id").value
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
            listContainer.innerHTML = `<tr><td colspan="6" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderUserProfiles(items) {
        const container = document.getElementById("user-profile-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="6" class="no-data-row">暂无相关用户画像记录</td></tr>`;
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
        listContainer.innerHTML = `<tr><td colspan="3" class="loading-row"><span class="loader"></span> 加载数据中...</td></tr>`;
        if (!document.getElementById("profile-bot-name").value) {
            this.pagination.groupProfiles.total = 0;
            listContainer.innerHTML = `<tr><td colspan="3" class="no-data-row">暂无可用 Bot</td></tr>`;
            window.renderPagination("group-profile-pagination", this.pagination.groupProfiles, () => {});
            return;
        }

        const params = {
            page: this.pagination.groupProfiles.page,
            limit: this.pagination.groupProfiles.limit,
            bot_name: document.getElementById("profile-bot-name").value,
            group_or_user_id: document.getElementById("profile-group-id").value
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
            listContainer.innerHTML = `<tr><td colspan="3" class="no-data-row">加载数据失败: ${e.message}</td></tr>`;
        }
    },

    renderGroupProfiles(items) {
        const container = document.getElementById("group-profile-list");
        if (!items || items.length === 0) {
            container.innerHTML = `<tr><td colspan="3" class="no-data-row">暂无相关群聊画像记录</td></tr>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const encodedProfile = encodeURIComponent(item.profile || "");
            return `
                <tr>
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

    // Tab switching for Edit Media Modal and Clean Cache Modal
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
                
                // Toggle clean-cache-modal footer buttons visibility
                const modal = btn.closest("#clean-cache-modal");
                if (modal) {
                    const btnManualCalc = modal.querySelector("#btn-manual-calc");
                    const btnManualSubmit = modal.querySelector("#btn-manual-submit");
                    const btnAutoTrigger = modal.querySelector("#btn-auto-trigger");
                    const btnAutoSave = modal.querySelector("#btn-auto-save");
                    if (tabName === "clean-manual") {
                        if (btnManualCalc) btnManualCalc.style.display = "inline-block";
                        if (btnManualSubmit) btnManualSubmit.style.display = "inline-block";
                        if (btnAutoTrigger) btnAutoTrigger.style.display = "none";
                        if (btnAutoSave) btnAutoSave.style.display = "none";
                    } else if (tabName === "clean-auto") {
                        if (btnManualCalc) btnManualCalc.style.display = "none";
                        if (btnManualSubmit) btnManualSubmit.style.display = "none";
                        if (btnAutoTrigger) btnAutoTrigger.style.display = "inline-block";
                        if (btnAutoSave) btnAutoSave.style.display = "inline-block";
                    }
                }
            }
        }
    });

    // Filter Listeners (Debounced)
    let filterTimeout;
    const filterInputIds = [
        "history-bot-name", "history-group-id", "history-user-id", "history-decision", "history-rag", "history-search",
        "memory-bot-name", "memory-group-id", "memory-associated-user-id", "memory-search",
        "media-type", "media-search",
        "profile-type", "profile-bot-name", "profile-group-id", "profile-user-id"
    ];

    filterInputIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            const eventType = el.tagName === "SELECT" ? "change" : "input";
            el.addEventListener(eventType, () => {
                clearTimeout(filterTimeout);
                filterTimeout = setTimeout(async () => {
                    const app = window.GiftiaApp;
                    const preserveSession = ![
                        "history-bot-name",
                        "memory-bot-name",
                        "profile-bot-name",
                        "profile-type"
                    ].includes(id);
                    if (app.activeTab === "chat-history") {
                        app.resetPagination("history");
                        await app.refreshScopedFilters("history", preserveSession);
                        await app.loadChatHistory();
                    } else if (app.activeTab === "memories") {
                        app.resetPagination("memories");
                        await app.refreshScopedFilters("memories", preserveSession);
                        await app.loadMemories();
                    } else if (app.activeTab === "media-captions") {
                        app.pagination.media.page = 1;
                        app.loadMedia();
                    } else if (app.activeTab === "profiles") {
                        app.activeSubTab = document.getElementById("profile-type")?.value || "user-profiles";
                        app.updateProfileFilterVisibility();
                        if (app.activeSubTab === "user-profiles") {
                            app.resetPagination("userProfiles");
                            await app.refreshScopedFilters("userProfiles", preserveSession);
                            await app.loadUserProfiles();
                        } else {
                            app.resetPagination("groupProfiles");
                            await app.refreshScopedFilters("groupProfiles", preserveSession);
                            await app.loadGroupProfiles();
                        }
                    }
                }, 300);
            });
        }
    });
});
