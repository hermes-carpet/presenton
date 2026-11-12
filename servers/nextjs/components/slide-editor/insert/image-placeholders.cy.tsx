import { createImageInsertContent } from "./insert-elements";
import { loadKonvaImage } from "../surface/exportAssets";

describe("Inserted image placeholders", () => {
  it("stays sharp when drawn at slide size on the editor canvas", () => {
    const image = createImageInsertContent("image").elements![0];
    cy.then(async () => {
      const loaded = await loadKonvaImage(image.data as string);
      expect(loaded).not.to.equal(null);
      const canvas = document.createElement("canvas");
      canvas.width = 610;
      canvas.height = 620;
      const context = canvas.getContext("2d")!;
      context.drawImage(loaded!, 0, 0, canvas.width, canvas.height);

      // The sun's left edge is x=23 in the 61-wide asset. Enlarging a
      // raster placeholder tenfold would blur this edge across many pixels.
      const edge = context.getImageData(220, 250, 20, 1).data;
      expect(edge[0]).to.equal(184);
      expect(edge[19 * 4]).to.equal(217);
      const blendedPixels = Array.from({ length: 20 }, (_, index) => edge[index * 4])
        .filter((red) => red > 184 && red < 217);
      expect(blendedPixels.length).to.be.at.most(2);
    });
  });
});
