import { mathSvgDataUri, measureMathLatex, renderMathHtml } from "@/lib/math";

describe("Equation rendering", () => {
  it("keeps equations measurable and exportable as SVG images", () => {
    cy.document().then(async (document) => {
      const latex = "\\frac{x^2}{2}+\\sqrt{y}";
      const size = measureMathLatex(latex, 24);
      expect(size.width).to.be.greaterThan(0);
      expect(size.height).to.be.greaterThan(0);
      const html = renderMathHtml(latex);
      const template = document.createElement("template");
      template.innerHTML = html;
      expect(template.content.querySelector("math")).not.to.equal(null);
      const source = mathSvgDataUri({ latex, width: 240, height: 80 });
      expect(source).to.be.a("string");
      const image = new Image();
      image.src = source!;
      await image.decode();
      expect(image.naturalWidth).to.equal(720);
      expect(image.naturalHeight).to.equal(240);
    });
  });
});
