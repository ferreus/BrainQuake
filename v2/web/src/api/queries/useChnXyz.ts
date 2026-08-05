import { getChnXyz } from "../endpoints";
import { qk } from "../queryKeys";
import { makeSubjectQuery } from "./factories";

export const useChnXyz = makeSubjectQuery(getChnXyz, qk.chnXyz, { staleTime: Infinity, retry: false });
