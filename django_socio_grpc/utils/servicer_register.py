from collections import OrderedDict
from importlib import import_module

from rest_framework.fields import IntegerField

from django_socio_grpc.mixins import get_default_grpc_methods
from django_socio_grpc.settings import grpc_settings


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(SingletonMeta, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class KnowMethods:
    LIST = "List"
    CREATE = "Create"
    RETRIEVE = "Retrieve"
    UPDATE = "Update"
    PARTIAL_UPDATE = "PartialUpdate"
    DESTROY = "Destroy"
    STREAM = "Stream"
    
    @classmethod
    def get_as_list(cls):
        return [cls.LIST, cls.CREATE, cls.RETRIEVE, cls.UPDATE, cls.PARTIAL_UPDATE, cls.DESTROY,  cls.STREAM]
    
    @classmethod
    def get_methods_no_custom_messages(cls):
        return [cls.CREATE, cls.UPDATE, cls.PARTIAL_UPDATE]


class RegistrySingleton(metaclass=SingletonMeta):
    """
    Registry Singleton is a singleton class allowing to grab all the service declared in grpc_settings.ROOT_HANDLERS_HOOK 
    and introspect django model and serializer to determine the proto to generate
    """

    _instances = {}

    def __init__(self):
        self.registered_app = OrderedDict()

    def register_service(self, Service):
        """"
        For each service register in ROOT_HANDLERS_HOOK we try to register its controller and its messages 
        """
        print("register_service", Service)

        service_instance = Service()

        # INFO - AM - 07/01/2022 - we can get the model associated to a service with it queryset
        # TODO - AM - 07/01/2022 - Find a way when using a Generic service and not a Model Service (decorator, attribute ? mostly attribute I think really simple, istropection with file name ossible too but way harder)
        # TODO - AM - 07/01/2022 - Answer from above: Add a method and a attr like serializer_class ang get_serializer_class in the generic service class 
        Model = service_instance.get_queryset().model
        app_name = Model._meta.app_label

        model_name = Model.__name__
        
        # INFO - AM - 07/01/2022 - Initialize the app in the project to be generated as a specific proto file
        if app_name not in self.registered_app:
            self.registered_app[app_name] = {
                "registered_controllers": OrderedDict(),
                "registered_messages": OrderedDict(),
            }

        # INFO - AM - 07/01/2022 - Choose the name of the controler
        # TODO - AM - 07/01/2022 - Maybe use an attr here like this no need to work on the generic_service. Maybe use the name of the class too
        controller_name = f"{model_name}Controller"

        print("REGISTER:")
        print("App name: ", app_name)
        print("Model", model_name)
        print("Controller", controller_name)

        self.set_controller_and_messages(
            app_name, model_name, controller_name, service_instance
        )

    def register_custom_action(self, *args, **kwargs):
        print("register_custom_action", args, kwargs)

    def set_controller_and_messages(
        self, app_name, model_name, controller_name, service_instance
    ):
        """
        Generate proto methods and messages for a service instance.
        First it try all know methods defined in the mixins used by ModelService. 
        If not existing it do nothing
        If existing we look if it already register with a decorator that will prevent the default behavior
        If not already register that mean we want to use the default behavior so we just go with that and call register_default_message_from_method
        """
        default_grpc_methods = get_default_grpc_methods(model_name)

        # print(self.registered_app[app_name])

        if controller_name not in self.registered_app[app_name]["registered_controllers"]:
            self.registered_app[app_name]["registered_controllers"][controller_name] = {}

        # INFO - AM - 07/01/2022 - we get the controller (the service methods already registered) to add the new method to it that all
        controller_object = self.registered_app[app_name]["registered_controllers"][
            controller_name
        ]

        for method in KnowMethods.get_as_list():
            if not getattr(service_instance, method, None):
                continue

            # If we already have registered this method for this controlleur (with a decorator) we do not use the default behavior
            if method in controller_object:
                continue
            
            # INFO - AM - 07/01/2022 - this is just the register of the methods with all the data necessary for the generation function in generators.py
            # default_grpc_methods[method] is a dictionnary see get_default_grpc_methods for more informations
            controller_object[method] = default_grpc_methods[method]

            self.register_default_message_from_method(app_name, method, service_instance)

        # print(self.registered_app[app_name]["registered_controllers"])
        # print(self.registered_app[app_name]["registered_messages"])

    def register_default_message_from_method(self, app_name, method, service_instance):
        """
        If we arrive in this method that mean that the developer use a generation with a default behavior
        for each king of method we check if this is the method passed as argument and:
            - get the serializer instance associated to the current action/method
            - determine from the serializer and the default method the grpc messages to create 
        """

        serializer_instance = self.get_serializer_instance_with_method(service_instance, method)

        if method in KnowMethods.get_methods_no_custom_messages():
            self.register_serializer_as_message_if_not_exist(app_name, serializer_instance)

        elif method == KnowMethods.LIST:

            self.register_list_serializer_as_message(
                app_name, service_instance, serializer_instance
            )

        elif method == KnowMethods.RETRIEVE:
            self.register_retrieve_serializer_as_message(
                app_name, service_instance, serializer_instance
            )
        
        elif method == KnowMethods.DESTROY:
            self.register_destroy_serializer_as_message(
                app_name, service_instance, serializer_instance
            )

        elif method == KnowMethods.STREAM:
            self.register_stream_serializer_as_message(
                app_name, serializer_instance
            )

        else:
            raise Exception(f"You are registering a service with the method {method} but this methods does not have a decorator and is not in our default supported methods: {KnowMethods.get_as_list()}")


    def get_serializer_instance_with_method(self, service_instance, method):
        """
        Assign to the service instance the current action to be able to anticipate case where a service has different serializer class returned
        then call get_serializer_class and return an instance of it for generating message by instrospecting
        """
        service_instance.action = method.lower()
        SerializerClass = service_instance.get_serializer_class()

        serializer_instance = SerializerClass()

        # fields = serializer_instance.get_fields()

        return serializer_instance

    def register_serializer_as_message_if_not_exist(self, app_name, serializer_instance):
        """
        Register a message if not already exsting in the registered_messages of an app_name
        This message need to be in a correct format that will be used by generators to transform it into generators
        """
        serializer_name = serializer_instance.__class__.__name__.replace("Serializer", "")
        if serializer_name not in self.registered_app[app_name]["registered_messages"]:
            self.registered_app[app_name]["registered_messages"][serializer_name] = list(
                serializer_instance.get_fields().items()
            )

            print("cicicicicicici ", list(serializer_instance.get_fields().items()))

            # for field in serializer_instance.get_fields():
            #     field_class, field_kwargs = serializer_instance.build_field(
            #         field[0], info, model, depth
            #     )

            # print(
            #     "icicic ",
            #     self.registered_app[app_name]["registered_messages"][serializer_name],
            # )

    def register_list_serializer_as_message(
        self, app_name, service_instance, serializer_instance, response_field_name="results"
    ):
        serializer_name = serializer_instance.__class__.__name__.replace("Serializer", "")
        pagination = service_instance.pagination_class
        if pagination is None:
            pagination = grpc_settings.DEFAULT_PAGINATION_CLASS is not None

        response_fields = [(response_field_name, f"repeated {serializer_name}")]
        if pagination:
            response_fields += [("count", IntegerField())]

        self.registered_app[app_name]["registered_messages"][
            f"{serializer_name}ListRequest"
        ] = []
        self.registered_app[app_name]["registered_messages"][
            f"{serializer_name}ListResponse"
        ] = response_fields

        self.register_serializer_as_message_if_not_exist(app_name, serializer_instance)

    def register_retrieve_serializer_as_message(
        self, app_name, service_instance, serializer_instance, retrieve_field_name=None
    ):
        retrieve_field = self.get_lookup_field_from_serializer(serializer_instance, service_instance, retrieve_field_name)

        serializer_name = serializer_instance.__class__.__name__.replace("Serializer", "")

        self.registered_app[app_name]["registered_messages"][
            f"{serializer_name}RetrieveRequest"
        ] = [retrieve_field]

        self.register_serializer_as_message_if_not_exist(app_name, serializer_instance)

    def register_destroy_serializer_as_message(
        self, app_name, service_instance, serializer_instance, destroy_field_name=None
    ):
    
        destroy_field = self.get_lookup_field_from_serializer(serializer_instance, service_instance, destroy_field_name)

        serializer_name = serializer_instance.__class__.__name__.replace("Serializer", "")

        self.registered_app[app_name]["registered_messages"][
            f"{serializer_name}DestroyRequest"
        ] = [destroy_field]

    def register_stream_serializer_as_message(
        self, app_name, serializer_instance
    ):
        serializer_name = serializer_instance.__class__.__name__.replace("Serializer", "")

        self.registered_app[app_name]["registered_messages"][
            f"{serializer_name}StreamRequest"
        ] = []

    def get_lookup_field_from_serializer(self, serializer_instance, service_instance, field_name=None):
        """
        Find the field associated to the lookup field
        serializer_instance: instance of the serializer used in this service where the lookup field should be present
        service_instance: the service instance itself where we can introspect for lookupfield
        field_name: If e do not want to use the default lookup field of the service but a specific field we just have to specify this params

        return: iterable: [str, <drf.serializers.Field>]
        """
        if field_name is None: 
            field_name = service_instance.get_lookup_request_field()

        # TODO - AM - 07/01/2022 - Check if the fied name in the existing field 
        if field_name not in serializer_instance.fields:
            raise Exception(f"Trying to build a Retrieve or Destroy request with retrieve field named: {field_name} but this field is not existing in the serializer: {serializer_instance.__class__.__name__}")
        
        # INFO - AM - 07/01/2022 - to match the format retuned by get_fields used for the generation we need to return an iterable with first element field_name and second element Instance of the Field class
        return [field_name, serializer_instance.fields[field_name]]


class AppHandlerRegistry:
    def __init__(self, app_name, server, service_folder="services", grpc_folder="grpc"):
        self.app_name = app_name
        self.server = server
        self.service_folder = service_folder
        self.grpc_folder = grpc_folder

    def register(self, model_name):
        if self.service_folder:
            model_service_path = (
                f"{self.app_name}.{self.service_folder}.{model_name.lower()}_service"
            )
        else:
            model_service_path = f"{self.app_name}.services"
        Model = getattr(
            import_module(model_service_path),
            f"{model_name}Service",
        )

        if self.server is None:
            service_registry = RegistrySingleton()
            service_registry.register_service(Model)
            return

        pb2_grpc = import_module(
            f"{self.app_name}.{self.grpc_folder}.{self.app_name}_pb2_grpc"
        )
        add_server = getattr(pb2_grpc, f"add_{model_name}ControllerServicer_to_server")

        add_server(Model.as_servicer(), self.server)
