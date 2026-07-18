import { state } from '../state.js';

export async function loadMemories() {
    const listContainer = document.getElementById("memory-list");
    listContainer.innerHTML = `<div class="loading-row"><span class="loader"></span> 加载数据中...</div>`;
    if (!document.getElementById("memory-bot-name").value) {
        state.pagination.memories.total = 0;
        listContainer.innerHTML = `<div class="no-data-row">暂无可用 Bot</div>`;
        window.renderPagination("memory-pagination", state.pagination.memories, () => {});
        return;
    }

    const params = {
        page: state.pagination.memories.page,
        limit: state.pagination.memories.limit,
        bot_name: document.getElementById("memory-bot-name").value,
        group_or_user_id: document.getElementById("memory-group-id").value,
        associated_user_id: document.getElementById("memory-associated-user-id").value,
        search: document.getElementById("memory-search").value
    };

    try {
        const res = await window.apiGet("/memories", params);
        if (res.status === "success" && res.data) {
            state.pagination.memories.total = res.data.total;
            renderMemories(res.data.items, res.data.user_id_to_name || {});
            window.renderPagination("memory-pagination", state.pagination.memories, (page) => {
                state.pagination.memories.page = page;
                loadMemories();
            });
        } else {
            throw new Error(res.message || "请求失败");
        }
    } catch (e) {
        listContainer.innerHTML = `<div class="no-data-row">加载数据失败: ${e.message}</div>`;
    }
}

export function renderMemories(items, userIdToName = {}) {
    const container = document.getElementById("memory-list");
    if (!items || items.length === 0) {
        container.innerHTML = `<div class="no-data-row">暂无相关长期记忆记录</div>`;
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

        // Render associated user tags
        let associatedUsersHtml = "";
        const uniqueUserIds = [...new Set(associatedUserIdsArray.length > 0 ? associatedUserIdsArray : (fallbackUserId ? [fallbackUserId] : []))];
        if (uniqueUserIds.length > 0) {
            associatedUsersHtml = uniqueUserIds.map(uid => {
                const cleanUid = String(uid).trim();
                const nickname = userIdToName[cleanUid];
                if (nickname) {
                    return `<span class="user-pill" onclick="window.filterMemoryByUser('${cleanUid}')" title="ID: ${cleanUid} (点击筛选)">${window.escapeHtml(nickname)}</span>`;
                } else {
                    return `<span class="user-pill user-pill-raw" onclick="window.filterMemoryByUser('${cleanUid}')" title="ID: ${cleanUid} (点击筛选)">${cleanUid}</span>`;
                }
            }).join("");
        } else {
            associatedUsersHtml = `<span class="muted-text" style="font-size: 11px;">无关联用户</span>`;
        }

        // Importance rating category mapping
        const importance = Number(item.importance || 5);
        let importanceCategory = "medium";
        if (importance <= 3) {
            importanceCategory = "low";
        } else if (importance >= 8) {
            importanceCategory = "high";
        }

        const hitCount = Number(item.hit_count || 0);
        const lastHitAt = item.last_hit_at ? window.formatDate(item.last_hit_at) : "从未命中";
        const formattedCreatedAt = window.formatDate(item.created_at);

        // Collapsible check
        const textContent = item.text || "";
        const isLong = textContent.length > 120 || (textContent.match(/\n/g) || []).length >= 3;

        return `
            <div class="memory-card">
                <div class="memory-card-header">
                    <div class="memory-importance-badge importance-${importanceCategory}">
                        <span class="importance-dot"></span>
                        重要度 ${importance}
                    </div>
                    <div class="memory-activity-badge" title="上次命中: ${lastHitAt}">
                        <span class="activity-icon">⚡</span>
                        <span>${hitCount} 次命中</span>
                    </div>
                </div>

                <div class="memory-card-body">
                    <div class="memory-text-container collapsed" id="memory-text-${item.memory_id}">
                        ${window.escapeHtml(textContent).replace(/\n/g, '<br>')}
                    </div>
                    ${isLong ? `
                        <button class="btn-text-toggle" id="btn-toggle-${item.memory_id}" onclick="window.toggleMemoryText('${item.memory_id}')">
                            展开全部
                        </button>
                    ` : ''}
                </div>

                <div class="memory-card-footer">
                    <div class="memory-associated-users">
                        ${associatedUsersHtml}
                    </div>
                    <div class="memory-actions">
                        <button class="btn-icon-action" onclick="window.openEditMemoryModal('${item.memory_id}', '${item.bot_name}', '${item.group_or_user_id}', '${encodedText}', '${associatedUserIdsList}', ${importance})" title="编辑">
                            <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        </button>
                        <button class="btn-icon-action danger" onclick="window.deleteMemory('${item.memory_id}')" title="删除">
                            <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        </button>
                    </div>
                </div>

                <div class="memory-time-details">
                    <span>创建于 ${formattedCreatedAt}</span>
                    ${hitCount > 0 ? `<span class="muted-text" title="上次命中: ${lastHitAt}">上次命中: ${lastHitAt.split(' ')[0]}</span>` : ''}
                </div>
            </div>
        `;
    }).join("");
}
