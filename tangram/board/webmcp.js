export async function registerWebMcpTools(tools) {
    const documentContext = document.modelContext;
    const navigatorContext = navigator.modelContext;
    const context = documentContext ?? navigatorContext;
    if (!context?.registerTool) {
        return { available: false, surface: "unavailable", dispose() { } };
    }
    const controller = new AbortController();
    try {
        await Promise.all(tools.map((tool) => context.registerTool(tool, { signal: controller.signal })));
    }
    catch (error) {
        controller.abort();
        throw error;
    }
    return {
        available: true,
        surface: documentContext ? "document.modelContext" : "navigator.modelContext",
        dispose: () => controller.abort(),
    };
}
