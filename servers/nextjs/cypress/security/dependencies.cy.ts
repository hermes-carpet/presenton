import createDOMPurify from "dompurify";

describe("Slide sanitization", () => {
  it("removes executable HTML and SVG while preserving slide content", () => {
    cy.window().then((window) => {
      const purify = createDOMPurify(window);
      const clean = purify.sanitize(
        '<h2 data-slide-title="true">Quarterly results</h2>' +
        '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" onload="alert(1)"/></svg>' +
        '<img src="/slide.png" onerror="alert(1)">' +
        '<script>alert(1)</script><a href="javascript:alert(1)">Details</a>',
        { USE_PROFILES: { html: true, svg: true, svgFilters: true } },
      );
      const template = window.document.createElement("template");
      template.innerHTML = clean;
      expect(template.content.querySelector("h2")?.textContent).to.equal("Quarterly results");
      expect(template.content.querySelector("h2")?.getAttribute("data-slide-title")).to.equal("true");
      expect(template.content.querySelector("circle")?.getAttribute("r")).to.equal("8");
      expect(template.content.querySelector("img")?.getAttribute("src")).to.equal("/slide.png");
      expect(template.content.querySelector("script, [onload], [onerror], a[href]")).to.equal(null);
    });
  });
});
