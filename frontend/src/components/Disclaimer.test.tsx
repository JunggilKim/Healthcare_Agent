import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";

import { Disclaimer } from "./Disclaimer";

test("shows the mandatory non-clinical-use statement", () => {
  render(<Disclaimer />);
  expect(screen.getByRole("complementary", { name: "의료 및 데이터 안전 고지" })).toHaveTextContent(
    "최종 참여 자격을 결정하지 않습니다",
  );
});

