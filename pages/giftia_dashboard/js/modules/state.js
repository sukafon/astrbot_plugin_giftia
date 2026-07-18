// Giftia State Module

export const state = {
    activeTab: "chat-history",
    activeSubTab: "user-profiles",

    // Pagination states
    pagination: {
        history: { page: 1, limit: 15, total: 0 },
        memories: { page: 1, limit: 15, total: 0 },
        media: { page: 1, limit: 12, total: 0 },
        forwards: { page: 1, limit: 15, total: 0 },
        userProfiles: { page: 1, limit: 15, total: 0 },
        groupProfiles: { page: 1, limit: 15, total: 0 },
        tokenLogs: { page: 1, limit: 15, total: 0 }
    },

    loadedOriginalMediaG: new Set(),
    filterOptions: {},
};
