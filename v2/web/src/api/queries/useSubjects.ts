import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createSubject, deleteSubject, listSubjects } from "../endpoints";
import type { ReconType } from "../types";

export function useSubjects() {
  return useQuery({
    queryKey: ["subjects"],
    queryFn: listSubjects,
  });
}

export function useCreateSubject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, reconType }: { name: string; reconType?: ReconType }) =>
      createSubject(name, reconType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["subjects"] });
    },
  });
}

export function useDeleteSubject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteSubject(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["subjects"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
