from unittest.mock import Mock, patch

from models.lstm import LSTMModel


def test_load_uses_weights_only() -> None:
    model = LSTMModel(input_size=10, hidden_size=2, num_layers=1)
    state = model.model.state_dict()
    with patch("models.lstm.torch.load", return_value=state) as loader:
        model.load("received.pth")
    loader.assert_called_once()
    assert loader.call_args.kwargs["weights_only"] is True
