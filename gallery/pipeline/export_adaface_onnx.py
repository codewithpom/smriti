"""Local one-off export; torch and AdaFace source/weights are NOT runner dependencies.

python export_adaface_onnx.py checkpoint.ckpt --model-code /local/iresnet/model.py
The model source must expose IR_101, like the IR_50 source used by adaface_ab.py.
"""

import argparse
import importlib.util
from pathlib import Path
import sys
import types


def export(checkpoint, model_code, output):
    import torch
    torch.set_num_threads(2)
    if "fvcore" not in sys.modules:
        fv, fn = types.ModuleType("fvcore"), types.ModuleType("fvcore.nn")
        fn.flop_count = lambda *a, **k: None
        sys.modules.update({"fvcore": fv, "fvcore.nn": fn})
    spec = importlib.util.spec_from_file_location("adaface_ir_model", model_code)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    net = mod.IR_101(input_size=(112, 112), output_dim=512)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = state.get("state_dict", state)
    expected = set(net.state_dict())
    for prefix in ("model.net.", "model.", "module.", ""):
        weights = {k[len(prefix):]: v for k, v in state.items()
                   if k.startswith(prefix) and k[len(prefix):] in expected}
        if set(weights) == expected:
            break
    net.load_state_dict(weights, strict=True)
    net.eval()

    class Embedding(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, images):
            result = self.model(images)
            return result[0] if isinstance(result, (tuple, list)) else result

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(Embedding(net).eval(), torch.zeros(2, 3, 112, 112), str(output),
                          opset_version=17, input_names=["images"], output_names=["embeddings"],
                          dynamic_axes={"images": {0: "batch"}, "embeddings": {0: "batch"}},
                          dynamo=False)
    print("exported 1 model")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--model-code", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / ".models" / "adaface_ir101.onnx")
    args = parser.parse_args(argv)
    export(args.checkpoint, args.model_code, args.output)


if __name__ == "__main__":
    main()
