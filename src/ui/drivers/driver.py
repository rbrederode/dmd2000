from abc import ABC, abstractmethod

class UIDriver(ABC):

    def close(self) -> None:
        """Release owned resources when replaced or stopped; default owns none."""
        pass

    @abstractmethod
    def publish(self, model_dict: dict) -> None:
        pass
