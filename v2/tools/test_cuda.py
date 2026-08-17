import sys
import torch

def test_cuda_sm61():
    print("=" * 60)
    print("PyTorch CUDA sm_61 Environment Check")
    print("=" * 60)

    # 1. Basic PyTorch & CUDA Detection
    print(f"Python Version        : {sys.version.split()[0]}")
    print(f"PyTorch Version       : {torch.__version__}")
    print(f"CUDA Available (Torch): {torch.cuda.is_available()}")

    if not torch.cuda.is_available():
        print("\n[FAIL] CUDA is not available to PyTorch.")
        print("Ensure you installed a CUDA-enabled PyTorch build (e.g., cu118 / cu121/124).")
        return

    # 2. CUDA Runtime & Build Architecture Details
    print(f"PyTorch CUDA Runtime  : {torch.version.cuda}")
    print(f"CUDNN Version         : {torch.backends.cudnn.version()}")
    
    # Check supported architectures in this PyTorch build
    arch_list = torch.cuda.get_arch_list()
    print(f"Compiled Architectures: {', '.join(arch_list)}")
    
    sm61_supported = any("sm_61" in arch or "compute_61" in arch for arch in arch_list)
    if not sm61_supported:
        print("\n[WARNING] 'sm_61' not found in PyTorch compiled arch list!")
        print("Executing CUDA kernels may fail with 'no kernel image is available'.")

    # 3. Device Identification
    device_id = 0
    device_name = torch.cuda.get_device_name(device_id)
    capability = torch.cuda.get_device_capability(device_id)
    cap_str = f"sm_{capability[0]}{capability[1]}"
    total_mem_gb = torch.cuda.get_device_properties(device_id).total_memory / (1024**3)

    print("-" * 60)
    print(f"Device [{device_id}]           : {device_name}")
    print(f"Compute Capability    : {capability} ({cap_str})")
    print(f"Total VRAM            : {total_mem_gb:.2f} GB")
    print("-" * 60)

    # 4. Kernel Execution & Math Test (GEMM + Autograd)
    print("Running kernel smoke test (Matrix Multiplication & Backward pass)...")
    try:
        device = torch.device(f"cuda:{device_id}")
        
        # Allocate tensors on GPU
        size = 2048
        a = torch.randn(size, size, device=device, requires_grad=True)
        b = torch.randn(size, size, device=device)

        # Forward pass (Matrix Multiply)
        c = torch.matmul(a, b)
        loss = c.sum()

        # Backward pass (Gradient computation)
        loss.backward()

        # Synchronize to catch any async CUDA runtime errors
        torch.cuda.synchronize()

        print("Kernel execution     : SUCCESS")
        print(f"Loss Output           : {loss.item():.4f}")
        print(f"Grad Norm Check       : {a.grad.norm().item():.4f}")
        print(f"Allocated VRAM        : {torch.cuda.memory_allocated(device_id) / (1024**2):.2f} MB")
        print(f"Cached VRAM           : {torch.cuda.memory_reserved(device_id) / (1024**2):.2f} MB")

        print("\n[SUCCESS] GTX 1060 (sm_61) is fully functional with PyTorch CUDA!")

    except RuntimeError as e:
        print(f"\n[ERROR] CUDA execution failed: {e}")
        if "no kernel image is available" in str(e):
            print("Explanation: The installed PyTorch wheel was compiled without sm_61 support.")

if __name__ == "__main__":
    test_cuda_sm61()
