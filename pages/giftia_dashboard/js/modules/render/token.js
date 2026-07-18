import { initializeScopedView } from '../filters.js';

export async function initializeTokenStatsTab() {
    await initializeScopedView("tokenLogs");
}

export async function loadTokenStatsSummary() {
    const botName = document.getElementById("token-bot-name").value;
    const groupId = document.getElementById("token-group-id").value;
    const timeRange = document.getElementById("token-time-range").value;

    const params = {};
    if (botName) params.bot_name = botName;
    if (groupId) params.group_or_user_id = groupId;
    if (timeRange) params.time_range = timeRange;

    try {
        const res = await window.apiGet("/token/stats", params);
        if (res.status === "success" && res.stats) {
            renderTokenStatsSummary(res.stats);
        }
    } catch (e) {
        console.error("加载 Token 统计概要失败", e);
    }
}

export function renderTokenStatsSummary(stats) {
    const summary = stats.summary || {};

    // Total numbers
    document.getElementById("stat-total-tokens").textContent = Number(summary.total_tokens || 0).toLocaleString();
    document.getElementById("stat-tts-chars").textContent = Number(summary.total_chars_tts || 0).toLocaleString();
    document.getElementById("stat-reply-tokens").textContent = Number(summary.reply_tokens || 0).toLocaleString();
    document.getElementById("stat-decision-tokens").textContent = Number(summary.decision_tokens || 0).toLocaleString();

    // Set title breakdowns on hover
    const totalCard = document.getElementById("stat-total-tokens").parentElement;
    if (totalCard) {
        totalCard.setAttribute("title", `输入 (Prompt): ${Number(summary.total_prompt_tokens || 0).toLocaleString()}\n输出 (Completion): ${Number(summary.total_completion_tokens || 0).toLocaleString()}`);
    }
    const replyCard = document.getElementById("stat-reply-tokens").parentElement;
    if (replyCard) {
        replyCard.setAttribute("title", `输入 (Prompt): ${Number(summary.reply_prompt_tokens || 0).toLocaleString()}\n输出 (Completion): ${Number(summary.reply_completion_tokens || 0).toLocaleString()}`);
    }
    const decisionCard = document.getElementById("stat-decision-tokens").parentElement;
    if (decisionCard) {
        decisionCard.setAttribute("title", `输入 (Prompt): ${Number(summary.decision_prompt_tokens || 0).toLocaleString()}\n输出 (Completion): ${Number(summary.decision_completion_tokens || 0).toLocaleString()}`);
    }

    // 1. Group Breakdown
    const groupCard = document.getElementById("card-group-breakdown");
    const groupContainer = document.getElementById("token-group-progress-bars");
    const groupId = document.getElementById("token-group-id").value;

    if (!groupId) {
        if (groupCard) groupCard.style.display = "block";
        const groups = stats.by_group || [];
        const maxGroupTokens = groups.reduce((sum, g) => sum + (g.total_tokens || 0), 0) || 1;

        if (groups.length === 0) {
            groupContainer.innerHTML = '<div style="font-size: 0.85rem; color: var(--font-secondary); text-align: center; padding: 20px 0;">暂无数据</div>';
        } else {
            groupContainer.innerHTML = groups.slice(0, 6).map((g, idx) => {
                const tokens = g.total_tokens || 0;
                const pct = ((tokens / maxGroupTokens) * 100).toFixed(1);
                const colors = ['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#0ea5e9'];
                const barColor = colors[idx % colors.length];

                return `
                    <div class="token-stat-row">
                        <div class="token-stat-info align-center">
                            <span class="token-stat-name monospace" title="${g.group_or_user_id || '私聊/系统'}">${g.group_or_user_id || '私聊/系统'}</span>
                            <span class="token-stat-value">${tokens.toLocaleString()} Tokens (${pct}%)</span>
                        </div>
                        <div class="progress-bar-container">
                            <div class="progress-bar-fill" style="width: ${pct}%; background-color: ${barColor};"></div>
                        </div>
                    </div>
                `;
            }).join('');
        }
    } else {
        if (groupCard) groupCard.style.display = "none";
    }

    // 2. Provider / Model Breakdown
    const modelContainer = document.getElementById("token-model-progress-bars");
    const modelTitle = document.getElementById("model-breakdown-title");

    if (modelTitle) {
        modelTitle.textContent = groupId ? "该会话下按供应商 / 模型 Token 占比" : "按供应商 / 模型 Token 占比";
    }

    const models = stats.by_model || [];
    const maxModelTokens = models.reduce((sum, m) => sum + (m.total_tokens || 0), 0) || 1;

    if (models.length === 0) {
        modelContainer.innerHTML = '<div style="font-size: 0.85rem; color: var(--font-secondary); text-align: center; padding: 20px 0;">暂无数据</div>';
    } else {
        modelContainer.innerHTML = models.slice(0, 6).map((m, idx) => {
            const tokens = m.total_tokens || 0;
            const pct = ((tokens / maxModelTokens) * 100).toFixed(1);

            // Determine model name as main text and provider id as subtext.
            let mainText = m.model_name || '未知';
            let subText = m.provider_id || '';

            // If provider_id contains '/', it's in the form 'provider/model'.
            // In that case, we can extract the provider as subText and the model as mainText.
            if (subText && subText.includes('/')) {
                const parts = subText.split('/');
                subText = parts[0];
                mainText = parts.slice(1).join('/');
            }

            const providerName = m.provider_id ? (m.provider_id.includes('/') ? m.provider_id : `${m.provider_id} (${mainText})`) : mainText;
            const colors = ['#8b5cf6', '#6366f1', '#10b981', '#f59e0b', '#ec4899', '#0ea5e9'];
            const barColor = colors[idx % colors.length];

            return `
                <div class="token-stat-row help-cursor" title="输入 (Prompt): ${Number(m.prompt_tokens || 0).toLocaleString()}\n输出 (Completion): ${Number(m.completion_tokens || 0).toLocaleString()}">
                    <div class="token-stat-info align-start">
                        <div class="token-stat-name-col">
                            <span class="token-stat-name bold" title="${providerName}">${mainText}</span>
                            ${subText ? `<span class="token-stat-subname" title="${subText}">${subText}</span>` : ''}
                        </div>
                        <span class="token-stat-value margin-left">${tokens.toLocaleString()} Tokens (${pct}%)</span>
                    </div>
                    <div class="progress-bar-container">
                        <div class="progress-bar-fill" style="width: ${pct}%; background-color: ${barColor};"></div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // 3. Category Breakdown (Type progress bars) on Card C
    const typeContainer = document.getElementById("token-type-progress-bars");
    if (typeContainer) {
        const categories = [
            { key: 'reply_tokens', name: '大模型回复', class: 'reply' },
            { key: 'decision_tokens', name: '小模型判断', class: 'decision' },
            { key: 'image_caption_tokens', name: '图片转述', class: 'image_caption' },
            { key: 'sticker_analysis_tokens', name: '表情包分析', class: 'sticker_analysis' },
            { key: 'audio_caption_tokens', name: '音频转述', class: 'audio_caption' },
            { key: 'passive_summary_tokens', name: '被动总结', class: 'passive_summary' }
        ];

        const maxTypeTokens = categories.reduce((sum, cat) => sum + (summary[cat.key] || 0), 0) || 1;

        typeContainer.innerHTML = categories.map(cat => {
            const tokens = summary[cat.key] || 0;
            const pct = ((tokens / maxTypeTokens) * 100).toFixed(1);

            let hoverTitle = "";
            if (cat.key === 'reply_tokens') {
                hoverTitle = `输入 (Prompt): ${Number(summary.reply_prompt_tokens || 0).toLocaleString()}\n输出 (Completion): ${Number(summary.reply_completion_tokens || 0).toLocaleString()}`;
            } else if (cat.key === 'decision_tokens') {
                hoverTitle = `输入 (Prompt): ${Number(summary.decision_prompt_tokens || 0).toLocaleString()}\n输出 (Completion): ${Number(summary.decision_completion_tokens || 0).toLocaleString()}`;
            }

            const colorMap = {
                'reply': '#6366f1',
                'decision': '#10b981',
                'image_caption': '#f59e0b',
                'sticker_analysis': '#14b8a6',
                'audio_caption': '#ec4899',
                'passive_summary': '#8b5cf6'
            };
            const barColor = colorMap[cat.class] || '#6366f1';

            return `
                <div class="token-stat-row ${hoverTitle ? 'help-cursor' : ''}" ${hoverTitle ? `title="${hoverTitle}"` : ''}>
                    <div class="token-stat-info align-center">
                        <span class="token-stat-name">${cat.name}</span>
                        <span class="token-stat-value no-mono">${tokens.toLocaleString()} Tokens (${pct}%)</span>
                    </div>
                    <div class="progress-bar-container">
                        <div class="progress-bar-fill" style="width: ${pct}%; background-color: ${barColor};"></div>
                    </div>
                </div>
            `;
        }).join('');
    }
}

export async function openTokenAutoCleanModal() {
    try {
        const res = await window.apiGet("/token/auto_clean/config");
        if (res.status === "success" && res.config) {
            document.getElementById("token-auto-clean-enabled").checked = res.config.enabled;
            document.getElementById("token-auto-clean-days").value = res.config.days;
            window.openModal("token-auto-clean-modal");
        } else {
            window.showToast("获取自动清理配置失败: " + (res.message || "未知错误"));
        }
    } catch (e) {
        window.showToast("获取自动清理配置失败: " + (e.message || e));
    }
}

export async function submitTokenAutoCleanConfig() {
    const enabled = document.getElementById("token-auto-clean-enabled").checked;
    const days = parseInt(document.getElementById("token-auto-clean-days").value) || 365;

    try {
        const res = await window.apiPost("/token/auto_clean/config", { enabled, days });
        if (res.status === "success") {
            window.showToast("配置保存成功");
            window.closeModal("token-auto-clean-modal");
        } else {
            window.showToast("保存配置失败: " + (res.message || "未知错误"));
        }
    } catch (e) {
        window.showToast("保存配置失败: " + (e.message || e));
    }
}

export function openTokenClearModal() {
    const botName = document.getElementById("token-bot-name").value;
    const groupId = document.getElementById("token-group-id").value;
    const timeRange = document.getElementById("token-time-range").value;

    let confirmMsg = "警告：这将永久删除符合当前筛选条件的所有 Token 和合成字符的记录，无法恢复！";
    if (!botName && !groupId && !timeRange) {
        confirmMsg = "警告：您未设置任何筛选条件，这将永久删除所有历史 Token 和合成字符的记录，无法恢复！";
    }

    window.showConfirm("确认清空日志", confirmMsg, async () => {
        try {
            const res = await window.apiPost("/token/clear", {
                bot_name: botName,
                group_or_user_id: groupId,
                time_range: timeRange
            });
            if (res.status === "success") {
                window.showToast(res.message || "清空成功");
                await loadTokenStatsSummary();
            } else {
                window.showToast("清除日志失败: " + (res.message || "未知错误"));
            }
        } catch (e) {
            window.showToast("清除日志失败: " + (e.message || e));
        }
    });
}
