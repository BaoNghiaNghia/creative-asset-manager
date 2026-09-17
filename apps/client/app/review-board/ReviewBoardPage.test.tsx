import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { mayViewReviewBoard } from "../components/WorkspaceNavigation";
import { ReviewBoardPage } from "./ReviewBoardPage";
describe("Review Board boundary",()=>{it("routes only the review-board path to the authenticated page",()=>{expect(routeForPath("/review-board")).toBe("review-board");expect(routeForPath("/review-board/")).toBe("review-board");expect(routeForPath("/share/public-id")).toBe("public-review");expect(routeForPath("/job-queue")).toBe("job-queue");});it("uses the exact read permission for navigation visibility",()=>{expect(mayViewReviewBoard(["public_review.read"])).toBe(true);expect(mayViewReviewBoard(["public_review.resolve"])).toBe(false);expect(mayViewReviewBoard(["public_review.manage"])).toBe(false);});it("shows an identity loading state before rendering board data",()=>{expect(renderToStaticMarkup(<ReviewBoardPage/>)).toContain("Loading your Review Board access");});});
