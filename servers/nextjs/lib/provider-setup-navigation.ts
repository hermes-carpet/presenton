export const providerSetupNavigationAction = (
    href: string | null,
    target: string | null,
    baseUrl: string
): "ignore" | "block" | "warn" => {
    if (!href || href === "#") return "ignore";
    try {
        const { protocol } = new URL(href, baseUrl);
        if (protocol !== "http:" && protocol !== "https:") return "block";
    } catch {
        return "block";
    }
    return target === "_blank" ? "ignore" : "warn";
};
